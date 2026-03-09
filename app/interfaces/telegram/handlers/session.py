import asyncio
import logging
import time
import uuid

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

    async def _send_output(self, message: Message, output: str, exit_code: int) -> None:
        formatted = Formatter.format_bash(output)
        exit_suffix = f"\n⚠️ Exit code: <code>{exit_code}</code>" if exit_code != 0 else ""

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

        stream_id = self.stream.generate_stream_id()
        shell_buffer = ""
        lock = asyncio.Lock()
        last_publish = 0.0

        async def on_shell_chunk(chunk: str) -> None:
            nonlocal shell_buffer, last_publish
            async with lock:
                shell_buffer += chunk
                now = time.monotonic()
                if now - last_publish < self.stream_update_interval:
                    return
                last_publish = now

                trimmed = shell_buffer[-3500:] if len(shell_buffer) > 3500 else shell_buffer
                rendered = Formatter.format_bash(trimmed)
                await self.stream.publish(
                    chat_id=message.chat.id,
                    stream_id=stream_id,
                    text=rendered,
                    parse_mode="HTML",
                )

        try:
            session = await self.service.enter_shell(user_id=user_id, on_stream_chunk=on_shell_chunk)
        except SessionUnavailableError:
            await message.answer("ℹ️ No active SSH session.")
            return
        except Exception as exc:
            await message.answer(f"❌ Failed to open shell: {Formatter.escape_html(str(exc))}")
            return

        self._shell_state[user_id] = {"stream_id": stream_id}
        await message.answer(
            f"🔁 Interactive shell opened on <code>{session.name}</code>.\n"
            f"Type commands directly. Use /exit to leave."
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

        if session.is_interactive:
            try:
                await self.service.shell_input(user_id=user_id, text=text)
            except Exception as exc:
                await message.answer(f"❌ Shell error: {Formatter.escape_html(str(exc))}")
            return

        await message.answer(f"⚡ <code>[{session.name}]</code> {Formatter.escape_html(text)}")
        await self.execute_command(message=message, command=text)
