import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.application.services import CommandService, ConnectionService, FileTransferService, GroupService, MacroService, MonitorService, SavedServerService
from app.core.logging import configure_logging
from app.core.settings import settings
from app.infrastructure.db.mongo import MongoDatabase
from app.infrastructure.security.fernet_cipher import FernetCipher
from app.infrastructure.ssh.asyncssh_runtime import SessionRegistry
from app.infrastructure.ssh.monitor_collector import SSHMonitorCollector
from app.infrastructure.telegram.hybrid_stream import HybridStreamPublisher
from app.interfaces.telegram.handlers import ConnectHandler, FileHandler, GroupHandler, MacroHandler, MonitorHandler, SavedServerHandler, SessionHandler, StartHandler

logger = logging.getLogger(__name__)


class BotApp:
    def __init__(self) -> None:
        settings.validate()
        configure_logging()

        self.bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dispatcher = Dispatcher(storage=MemoryStorage())

        self.mongo = MongoDatabase(mongo_uri=settings.mongo_uri, db_name=settings.mongo_db_name)
        self.cipher = FernetCipher(settings.encryption_key)
        self.sessions = SessionRegistry()
        self.monitor_collector = SSHMonitorCollector()
        self.stream_publisher = HybridStreamPublisher(bot=self.bot, bot_token=settings.bot_token)

        self.connection_service = ConnectionService(
            sessions=self.sessions,
            history_repo=self.mongo,
            server_repo=self.mongo,
            cipher=self.cipher,
        )
        self.command_service = CommandService(sessions=self.sessions, history_repo=self.mongo)
        self.saved_server_service = SavedServerService(
            sessions=self.sessions,
            history_repo=self.mongo,
            server_repo=self.mongo,
        )
        self.group_service = GroupService(group_repo=self.mongo, server_repo=self.mongo)
        self.macro_service = MacroService(macro_repo=self.mongo)
        self.file_service = FileTransferService(sessions=self.sessions)
        self.monitor_service = MonitorService(sessions=self.sessions, collector=self.monitor_collector)

        self._timeout_task: asyncio.Task | None = None

        self._register_middleware()
        self._register_handlers()
        self._register_hooks()

    def _register_middleware(self) -> None:
        if not settings.allowed_users:
            return

        @self.dispatcher.message.outer_middleware()
        async def allow_users_only(handler, event, data):
            if event.from_user and event.from_user.id not in settings.allowed_users:
                await event.answer("🚫 You are not authorized to use this bot.")
                return
            return await handler(event, data)

        logger.info("Access control enabled for user IDs: %s", settings.allowed_users)

    def _register_handlers(self) -> None:
        start_handler = StartHandler()
        connect_handler = ConnectHandler(
            service=self.connection_service,
            stream_publisher=self.stream_publisher,
            stream_update_interval=settings.stream_update_interval,
        )
        session_handler = SessionHandler(
            service=self.command_service,
            stream_publisher=self.stream_publisher,
            stream_update_interval=settings.stream_update_interval,
        )
        server_handler = SavedServerHandler(service=self.saved_server_service, connection_service=self.connection_service)
        group_handler = GroupHandler(service=self.group_service)
        macro_handler = MacroHandler(service=self.macro_service)
        file_handler = FileHandler(service=self.file_service)
        monitor_handler = MonitorHandler(service=self.monitor_service)

        macro_handler.set_execute_callback(session_handler.execute_command)

        self.dispatcher.include_router(start_handler.router)
        self.dispatcher.include_router(connect_handler.router)
        self.dispatcher.include_router(server_handler.router)
        self.dispatcher.include_router(group_handler.router)
        self.dispatcher.include_router(macro_handler.router)
        self.dispatcher.include_router(file_handler.router)
        self.dispatcher.include_router(monitor_handler.router)
        self.dispatcher.include_router(session_handler.router)

    def _register_hooks(self) -> None:
        self.dispatcher.startup.register(self.on_startup)
        self.dispatcher.shutdown.register(self.on_shutdown)

    async def on_startup(self, bot: Bot) -> None:
        await self.mongo.connect()
        logger.info("Mongo connected")

        try:
            await bot.set_my_commands(StartHandler.bot_commands())
            logger.info("Registered %d Telegram bot commands", len(StartHandler.COMMAND_CATALOG))
        except Exception as exc:
            logger.warning("Failed to register Telegram bot commands: %s", exc)

        if settings.session_timeout_minutes > 0:
            self._timeout_task = asyncio.create_task(self._timeout_watchdog())
            logger.info("Session timeout watchdog enabled (%d min)", settings.session_timeout_minutes)

        me = await bot.get_me()
        logger.info("Bot started: @%s", me.username)

    async def on_shutdown(self, _: Bot) -> None:
        if self._timeout_task:
            self._timeout_task.cancel()
            try:
                await self._timeout_task
            except asyncio.CancelledError:
                pass

        await self.sessions.close_all()
        await self.mongo.close()
        logger.info("Shutdown complete")

    async def _timeout_watchdog(self) -> None:
        try:
            while True:
                await asyncio.sleep(60)
                timed_out = await self.connection_service.timeout_cleanup(settings.session_timeout_minutes)
                for user_id, session_name, host, port in timed_out:
                    try:
                        await self.bot.send_message(
                            user_id,
                            (
                                f"⏰ Session <code>{session_name}</code> ({host}:{port}) "
                                f"auto-disconnected after {settings.session_timeout_minutes} minutes of inactivity."
                            ),
                        )
                    except Exception:
                        pass
        except asyncio.CancelledError:
            pass

    def run(self) -> None:
        asyncio.run(self.start())

    async def start(self) -> None:
        await self.dispatcher.start_polling(self.bot)
