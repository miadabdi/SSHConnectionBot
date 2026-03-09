import logging
from base64 import b64decode, b64encode
from io import BytesIO

from aiogram import Bot, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.application.services import ConnectionService
from app.domain.errors import NotFoundError, ValidationError
from app.interfaces.telegram.states import ConnectForm
from app.utils.validators import is_valid_host, is_valid_port, is_valid_slug

logger = logging.getLogger(__name__)


class ConnectHandler:
    def __init__(self, service: ConnectionService) -> None:
        self.service = service
        self.router = Router(name="connect")
        self._register()

    def _register(self) -> None:
        self.router.message.register(self.cmd_connect, Command("connect"))
        self.router.message.register(self.cmd_disconnect, Command("disconnect"))
        self.router.message.register(self.cmd_switch, Command("switch"))
        self.router.message.register(self.cmd_status, Command("status"))
        self.router.message.register(self.cmd_history, Command("history"))

        self.router.message.register(self.process_name, StateFilter(ConnectForm.name))
        self.router.message.register(self.process_host, StateFilter(ConnectForm.host))
        self.router.message.register(self.process_port, StateFilter(ConnectForm.port))
        self.router.message.register(self.process_username, StateFilter(ConnectForm.username))
        self.router.callback_query.register(self.cb_auth_type, lambda c: c.data and c.data.startswith("auth:"))
        self.router.message.register(self.process_password, StateFilter(ConnectForm.password))
        self.router.message.register(self.process_key_file, StateFilter(ConnectForm.key_file))
        self.router.message.register(self.process_key_passphrase, StateFilter(ConnectForm.key_passphrase))

    async def cmd_connect(self, message: Message, state: FSMContext) -> None:
        await state.set_state(ConnectForm.name)
        await message.answer(
            "🌐 <b>New SSH Connection</b>\n\n"
            "Enter a session name (e.g. <code>prod</code>, <code>staging</code>):"
        )

    async def process_name(self, message: Message, state: FSMContext) -> None:
        name = (message.text or "").strip().lower()
        if not is_valid_slug(name):
            await message.answer("❌ Invalid session name. Use letters, numbers, hyphens, and underscores.")
            return

        if self.service.sessions.get(message.from_user.id, name):
            await message.answer(f"⚠️ Session <code>{name}</code> already exists.")
            return

        await state.update_data(name=name)
        await state.set_state(ConnectForm.host)
        await message.answer("🌐 Enter SSH host (IPv4/IPv6/hostname):")

    async def process_host(self, message: Message, state: FSMContext) -> None:
        host = (message.text or "").strip()
        if not is_valid_host(host):
            await message.answer("❌ Invalid host. Example: <code>192.168.1.10</code> or <code>server.example.com</code>")
            return

        await state.update_data(host=host)
        await state.set_state(ConnectForm.port)
        await message.answer("🔌 Enter SSH port (default: <code>22</code>):")

    async def process_port(self, message: Message, state: FSMContext) -> None:
        port_text = (message.text or "").strip()
        if not is_valid_port(port_text):
            await message.answer("❌ Invalid port. Enter a number between 1 and 65535.")
            return

        await state.update_data(port=int(port_text))
        await state.set_state(ConnectForm.username)
        await message.answer("👤 Enter SSH username:")

    async def process_username(self, message: Message, state: FSMContext) -> None:
        username = (message.text or "").strip()
        if not username:
            await message.answer("❌ Username cannot be empty.")
            return

        await state.update_data(username=username)
        await state.set_state(ConnectForm.auth_type)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔑 Password", callback_data="auth:password"),
                    InlineKeyboardButton(text="📄 Key File", callback_data="auth:key"),
                ]
            ]
        )
        await message.answer("🔐 Choose authentication method:", reply_markup=keyboard)

    async def cb_auth_type(self, callback: CallbackQuery, state: FSMContext) -> None:
        auth_type = callback.data.split(":", maxsplit=1)[1]
        await state.update_data(auth_type=auth_type)
        if auth_type == "password":
            await state.set_state(ConnectForm.password)
            await callback.message.edit_text("🔑 Enter SSH password:")
        else:
            await state.set_state(ConnectForm.key_file)
            await callback.message.edit_text("📄 Upload private key file (.pem / id_rsa):")
        await callback.answer()

    async def process_password(self, message: Message, state: FSMContext) -> None:
        password = (message.text or "").strip()
        if not password:
            await message.answer("❌ Password cannot be empty.")
            return

        try:
            await message.delete()
        except Exception:
            pass

        await self._do_connect(message=message, state=state, password=password)

    async def process_key_file(self, message: Message, state: FSMContext, bot: Bot) -> None:
        if not message.document:
            await message.answer("❌ Please upload a private key file.")
            return

        try:
            file = await bot.get_file(message.document.file_id)
            key_buffer = BytesIO()
            await bot.download_file(file.file_path, key_buffer)
            key_data = key_buffer.getvalue()
        except Exception as exc:
            await message.answer(f"❌ Failed to read key file: {exc}")
            return

        await state.update_data(key_data_b64=b64encode(key_data).decode())
        await state.set_state(ConnectForm.key_passphrase)
        await message.answer("🔐 Enter key passphrase, or send <code>-</code> if the key has no passphrase.")

    async def process_key_passphrase(self, message: Message, state: FSMContext) -> None:
        if message.text is None:
            await message.answer("❌ Enter the passphrase as text, or send <code>-</code> to skip.")
            return

        passphrase_text = message.text
        passphrase = None if passphrase_text.strip() == "-" else passphrase_text

        data = await state.get_data()
        encoded_key = data.get("key_data_b64")
        if not isinstance(encoded_key, str):
            await state.clear()
            await message.answer("❌ Key upload expired. Please run <code>/connect</code> again.")
            return

        try:
            key_data = b64decode(encoded_key.encode())
        except Exception:
            await state.clear()
            await message.answer("❌ Invalid key data. Please run <code>/connect</code> again.")
            return

        if passphrase is not None:
            try:
                await message.delete()
            except Exception:
                pass

        await self._do_connect(message=message, state=state, password=passphrase, key_data=key_data)

    async def _do_connect(
        self,
        message: Message,
        state: FSMContext,
        password: str | None = None,
        key_data: bytes | None = None,
    ) -> None:
        data = await state.get_data()
        await state.clear()

        name = data["name"]
        host = data["host"]
        port = data["port"]
        username = data["username"]

        status_message = await message.answer(
            f"⏳ Connecting to <code>{host}:{port}</code> as <code>{username}</code> "
            f"(session: <code>{name}</code>)..."
        )

        try:
            session = await self.service.connect(
                user_id=message.from_user.id,
                name=name,
                host=host,
                port=port,
                username=username,
                password=password,
                key_data=key_data,
            )
        except ValidationError as exc:
            await status_message.edit_text(f"❌ {exc}")
            return
        except Exception as exc:
            error_message = str(exc)
            if "Permission denied" in error_message or "Authentication" in error_message:
                rendered = "Authentication failed. Check your credentials."
            elif "Passphrase must be specified" in error_message:
                rendered = "This private key requires a passphrase."
            elif "Incorrect passphrase" in error_message:
                rendered = "Incorrect key passphrase."
            elif "timed out" in error_message or "Timeout" in error_message:
                rendered = "Connection timed out. Check host and port."
            elif "refused" in error_message:
                rendered = "Connection refused. Check SSH service availability."
            elif "No route" in error_message or "unreachable" in error_message:
                rendered = "Host unreachable."
            else:
                rendered = f"Connection failed: {error_message}"

            logger.warning("SSH connect failed for user %d: %s", message.from_user.id, error_message)
            await status_message.edit_text(f"❌ {rendered}")
            return

        active_count = len(self.service.sessions.get_all(message.from_user.id))
        await status_message.edit_text(
            f"✅ <b>Connected</b> (session: <code>{session.name}</code>)\n\n"
            f"🖥 Host: <code>{session.host}:{session.port}</code>\n"
            f"👤 User: <code>{session.username}</code>\n"
            f"🔐 Auth: {session.auth_type}\n"
            f"📊 Active sessions: {active_count}"
        )

    async def cmd_disconnect(self, message: Message, state: FSMContext) -> None:
        await state.clear()
        args = (message.text or "").split(maxsplit=1)

        if len(args) > 1 and args[1].strip().lower() == "all":
            count = await self.service.disconnect_all(message.from_user.id)
            await message.answer(f"🔌 Disconnected from all {count} session(s).")
            return

        target_name = args[1].strip().lower() if len(args) > 1 else None
        result = await self.service.disconnect(message.from_user.id, target_name)
        if not result.disconnected:
            await message.answer("ℹ️ No matching active session.")
            return

        await message.answer(
            f"🔌 Disconnected from <code>{result.name}</code> ({result.host}:{result.port})"
        )

    async def cmd_switch(self, message: Message) -> None:
        user_id = message.from_user.id
        args = (message.text or "").split(maxsplit=1)

        sessions = self.service.sessions.get_all(user_id)
        if not sessions:
            await message.answer("ℹ️ No active sessions. Use /connect to start one.")
            return

        if len(args) < 2:
            active = self.service.get_active_name(user_id)
            lines = ["🔀 <b>Active Sessions</b>\n"]
            for name, session in sessions.items():
                marker = " 👈" if name == active else ""
                lines.append(
                    f"{'🟢' if name == active else '⚪'} <code>{name}</code> — {session.host}:{session.port}{marker}"
                )
            lines.append("\nUse <code>/switch &lt;name&gt;</code> to switch.")
            await message.answer("\n".join(lines))
            return

        target = args[1].strip().lower()
        try:
            session = self.service.switch(user_id=user_id, name=target)
        except NotFoundError:
            await message.answer(f"❌ Session <code>{target}</code> not found.")
            return

        await message.answer(f"🔀 Switched to <code>{target}</code> ({session.host}:{session.port})")

    async def cmd_status(self, message: Message) -> None:
        user_id = message.from_user.id
        sessions = self.service.get_status(user_id)
        if not sessions:
            await message.answer("ℹ️ No active SSH sessions.")
            return

        active = self.service.get_active_name(user_id)
        lines = [f"📊 <b>Sessions</b> ({len(sessions)} active)\n"]
        for session in sessions:
            marker = " 👈 active" if session.name == active else ""
            mode = "🔁 interactive" if session.is_interactive else "📝 command"
            lines.append(
                f"{'🟢' if session.name == active else '⚪'} <b>{session.name}</b>{marker}\n"
                f"   🖥 {session.host}:{session.port} as {session.username}\n"
                f"   🔐 {session.auth_type} | {mode}"
            )

        await message.answer("\n".join(lines))

    async def cmd_history(self, message: Message) -> None:
        entries = await self.service.get_history(message.from_user.id)
        if not entries:
            await message.answer("ℹ️ No session history found.")
            return

        lines = ["📋 <b>Recent Sessions</b>\n"]
        for index, entry in enumerate(entries, start=1):
            status = "🟢" if entry.is_active else "⚪"
            created = entry.created_at.strftime("%Y-%m-%d %H:%M UTC")
            lines.append(
                f"{status} <b>{index}.</b> <code>{entry.session_name}</code> — "
                f"<code>{entry.host}:{entry.port}</code> as <code>{entry.username}</code>\n"
                f"    📅 {created}"
            )

        await message.answer("\n".join(lines))
