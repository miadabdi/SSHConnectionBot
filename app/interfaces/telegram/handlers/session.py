import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import default_state
from aiogram.types import CallbackQuery, Message

from app.application.services import CommandService
from app.domain.errors import SessionUnavailableError
from app.domain.ports import StreamPublisher
from app.utils.formatting import Formatter, MAX_MESSAGE_LENGTH
from app.utils.paging import OutputPager

logger = logging.getLogger(__name__)
StreamChunkCallback = Callable[[str], Awaitable[None]]


class SessionHandler:
    def __init__(
        self,
        service: CommandService,
        stream_publisher: StreamPublisher,
        stream_update_interval: float,
    ) -> None:
        self.service = service
        self.stream = stream_publisher
        self.stream_update_interval = stream_update_interval
        self.router = Router(name="session")
        self._paged_outputs: dict[str, OutputPager] = {}
        self._shell_state: dict[int, dict] = {}
        self._register()

    def _register(self) -> None:
        self.router.message.register(self.cmd_shell, Command("shell"))
        self.router.message.register(self.cmd_exit, Command("exit"))
        self.router.message.register(self.cmd_cancel, Command("cancel"))
        self.router.message.register(self.cmd_pwd, Command("pwd"))
        self.router.callback_query.register(self.cb_page, lambda c: c.data and c.data.startswith("page:"))
        self.router.callback_query.register(self.cb_noop, lambda c: c.data == "noop")
        self.router.message.register(self.handle_message, StateFilter(default_state))

    async def execute_command(self, message: Message, command: str) -> None:
        user_id = message.from_user.id
        chat_id = message.chat.id

        stream_id = self.stream.generate_stream_id()
        buffer = ""
        lock = asyncio.Lock()
        last_publish = 0.0

        async def on_stream(chunk: str) -> None:
            nonlocal buffer, last_publish
            async with lock:
                buffer += chunk
                now = time.monotonic()
                if now - last_publish < self.stream_update_interval:
                    return
                last_publish = now

                rendered = Formatter.format_bash(Formatter.truncate(buffer))
                await self.stream.publish(chat_id=chat_id, stream_id=stream_id, text=rendered, parse_mode="HTML")

        try:
            result = await self.service.execute(user_id=user_id, command=command, on_stream_chunk=on_stream)
        except SessionUnavailableError:
            await message.answer("ℹ️ No active SSH session. Use /connect first.")
            return
        except Exception as exc:
            text = str(exc)
            if "Disconnect" in text or "closed" in text:
                await message.answer("⚠️ SSH connection was lost. Use /connect to reconnect.")
            else:
                await message.answer(f"❌ Error:\n<pre>{Formatter.escape_html(text)}</pre>")
            return

        if result.output.strip():
            await self._send_output(message=message, output=result.output, exit_code=result.exit_code)
        else:
            status = "✅" if result.exit_code == 0 else f"⚠️ Exit code: <code>{result.exit_code}</code>"
            await message.answer(f"Command completed with no output.\n{status}")

    def _build_stream_callbacks(
        self,
        chat_id: int,
    ) -> tuple[StreamChunkCallback, Callable[[], Awaitable[None]], Callable[[], str]]:
        stream_id = self.stream.generate_stream_id()
        buffer = ""
        lock = asyncio.Lock()
        last_publish = 0.0

        async def publish(force: bool = False) -> None:
            nonlocal last_publish
            if not buffer.strip():
                return
            now = time.monotonic()
            if not force and now - last_publish < self.stream_update_interval:
                return
            last_publish = now
            rendered = Formatter.format_bash(Formatter.truncate(buffer))
            await self.stream.publish(chat_id=chat_id, stream_id=stream_id, text=rendered, parse_mode="HTML")

        async def on_stream(chunk: str) -> None:
            nonlocal buffer
            async with lock:
                buffer += chunk
                await publish(force=False)

        async def flush() -> None:
            async with lock:
                await publish(force=True)

        def get_buffer() -> str:
            return buffer

        return on_stream, flush, get_buffer

    async def _send_output(self, message: Message, output: str, exit_code: int | None) -> None:
        formatted = Formatter.format_bash(output)
        exit_suffix = ""
        if exit_code is not None and exit_code != 0:
            exit_suffix = f"\n⚠️ Exit code: <code>{exit_code}</code>"

        if len(formatted) + len(exit_suffix) <= MAX_MESSAGE_LENGTH:
            await message.answer(formatted + exit_suffix)
            return

        pager_id = uuid.uuid4().hex[:8]
        pager = OutputPager(output)
        self._paged_outputs[pager_id] = pager

        first_page = Formatter.format_bash(pager.get_page(1)) + exit_suffix
        keyboard = OutputPager.keyboard(current_page=1, total_pages=pager.total_pages, callback_prefix=f"page:{pager_id}")
        await message.answer(first_page, reply_markup=keyboard)

    async def cb_page(self, callback: CallbackQuery) -> None:
        parts = callback.data.split(":")
        if len(parts) != 3:
            await callback.answer()
            return

        pager_id = parts[1]
        page_number = int(parts[2])

        pager = self._paged_outputs.get(pager_id)
        if not pager:
            await callback.answer("Output expired")
            return

        text = Formatter.format_bash(pager.get_page(page_number))
        keyboard = OutputPager.keyboard(page_number, pager.total_pages, f"page:{pager_id}")

        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
        except Exception:
            pass
        await callback.answer()

    async def cb_noop(self, callback: CallbackQuery) -> None:
        await callback.answer()

    async def cmd_shell(self, message: Message) -> None:
        user_id = message.from_user.id
        session = self.service.sessions.get_active(user_id)
        if not session:
            await message.answer("ℹ️ No active SSH session. Use /connect first.")
            return

        if session.is_interactive:
            await message.answer("ℹ️ Already in interactive mode. Use /exit to leave.")
            return

        async def on_shell_chunk(chunk: str) -> None:
            # Command-reply shell mode intentionally suppresses raw terminal byte stream.
            return None

        try:
            session = await self.service.enter_shell(user_id=user_id, on_stream_chunk=on_shell_chunk)
        except SessionUnavailableError:
            await message.answer("ℹ️ No active SSH session.")
            return
        except Exception as exc:
            await message.answer(f"❌ Failed to open shell: {Formatter.escape_html(str(exc))}")
            return

        self._shell_state[user_id] = {"session": session.name, "lock": asyncio.Lock()}
        await message.answer(
            f"🔁 Interactive shell opened on <code>{session.name}</code>.\n"
            "Send commands directly; each message is sent to shell stdin.\n"
            "Output streams live and updates in place.\n"
            "Use /cancel for Ctrl+C, /pwd to show current directory, and /exit to leave."
        )

    async def cmd_exit(self, message: Message) -> None:
        user_id = message.from_user.id
        try:
            session = await self.service.exit_shell(user_id)
        except SessionUnavailableError:
            await message.answer("ℹ️ Not in interactive mode.")
            return

        self._shell_state.pop(user_id, None)
        await message.answer(f"📝 Exited interactive shell on <code>{session.name}</code>.")

    async def cmd_cancel(self, message: Message) -> None:
        user_id = message.from_user.id
        try:
            await self.service.shell_interrupt(user_id)
        except SessionUnavailableError:
            await message.answer("ℹ️ Not in interactive mode.")
            return
        except Exception as exc:
            await message.answer(f"❌ Shell error: {Formatter.escape_html(str(exc))}")
            return

        await message.answer("⛔ Sent Ctrl+C to the active shell.")

    async def cmd_pwd(self, message: Message) -> None:
        user_id = message.from_user.id
        try:
            cwd = await self.service.shell_get_cwd(user_id=user_id)
        except SessionUnavailableError:
            await message.answer("ℹ️ Not in interactive mode.")
            return
        except Exception as exc:
            await message.answer(f"❌ Shell error: {Formatter.escape_html(str(exc))}")
            return

        await message.answer(f"📁 <code>{Formatter.escape_html(cwd)}</code>")

    async def handle_message(self, message: Message) -> None:
        if not message.text:
            return

        user_id = message.from_user.id
        session = self.service.sessions.get_active(user_id)
        if not session:
            return

        text = message.text.strip()
        if not text:
            return

        if text.startswith("//"):
            text = text[1:]
        elif text.startswith("/"):
            await message.answer(
                "ℹ️ Commands starting with <code>/</code> are reserved for bot commands.\n"
                "To run a remote slash command, prefix it with <code>//</code>."
            )
            return

        if session.is_interactive:
            shell_state = self._shell_state.get(user_id)
            if not shell_state or shell_state.get("session") != session.name:
                shell_state = {"session": session.name, "lock": asyncio.Lock()}
                self._shell_state[user_id] = shell_state
            shell_lock: asyncio.Lock = shell_state.setdefault("lock", asyncio.Lock())

            if shell_lock.locked():
                try:
                    await self.service.shell_input(user_id=user_id, text=text)
                except Exception as exc:
                    await message.answer(f"❌ Shell error: {Formatter.escape_html(str(exc))}")
                    return
                return

            async with shell_lock:
                if self._looks_interactive_terminal_app(text):
                    await message.answer(
                        "⚠️ Full-screen interactive programs are not supported in Telegram shell "
                        "(e.g. vim/top/less)."
                    )
                    return

                on_stream, flush_stream, get_stream_buffer = self._build_stream_callbacks(chat_id=message.chat.id)
                try:
                    result = await self.service.shell_execute(
                        user_id=user_id,
                        command=text,
                        on_stream_chunk=on_stream,
                    )
                    await flush_stream()
                except Exception as exc:
                    if str(exc).strip() == "Command interrupted":
                        return
                    await message.answer(f"❌ Shell error: {Formatter.escape_html(str(exc))}")
                    return

                streamed_output = get_stream_buffer()
                if result.output.strip() and not streamed_output.strip():
                    await self._send_output(message=message, output=result.output, exit_code=result.exit_code)
                if not result.output.strip():
                    status = "✅" if result.exit_code == 0 else f"⚠️ Exit code: <code>{result.exit_code}</code>"
                    await message.answer(f"Command completed with no output.\n{status}")
            return

        await message.answer(f"⚡ <code>[{session.name}]</code> {Formatter.escape_html(text)}")
        await self.execute_command(message=message, command=text)

    @staticmethod
    def _looks_interactive_terminal_app(command: str) -> bool:
        token = command.strip().split(maxsplit=1)[0].lower() if command.strip() else ""
        return token in {"vim", "vi", "nano", "top", "htop", "less", "more", "man", "watch"}
