import asyncio
import html
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
        self.router.message.register(self.cmd_enter, Command("enter"))
        self.router.message.register(self.cmd_pwd, Command("pwd"))
        self.router.callback_query.register(self.cb_page, lambda c: c.data and c.data.startswith("page:"))
        self.router.callback_query.register(self.cb_noop, lambda c: c.data == "noop")
        self.router.message.register(self.handle_message, StateFilter(default_state))

    async def execute_command(self, message: Message, command: str) -> None:
        user_id = message.from_user.id
        chat_id = message.chat.id

        stream_ids = [self.stream.generate_stream_id()]
        last_pages: list[str] = []
        buffer = ""
        lock = asyncio.Lock()
        last_publish = 0.0
        delayed_publish_task: asyncio.Task | None = None

        async def publish(force: bool = False) -> None:
            nonlocal last_publish, last_pages
            if not buffer.strip():
                return
            now = time.monotonic()
            if not force and now - last_publish < self.stream_update_interval:
                return
            last_publish = now
            pages = self._format_stream_pages(buffer)
            for index, rendered in enumerate(pages):
                if index >= len(stream_ids):
                    stream_ids.append(self.stream.generate_stream_id())
                if index < len(last_pages) and last_pages[index] == rendered:
                    continue
                await self.stream.publish(
                    chat_id=chat_id,
                    stream_id=stream_ids[index],
                    text=rendered,
                    parse_mode="HTML",
                )
            last_pages = pages

        async def delayed_publish(delay: float) -> None:
            nonlocal delayed_publish_task
            try:
                await asyncio.sleep(delay)
                async with lock:
                    await publish(force=True)
            except asyncio.CancelledError:
                return
            finally:
                delayed_publish_task = None

        async def on_stream(chunk: str) -> None:
            nonlocal buffer, delayed_publish_task
            async with lock:
                buffer += chunk
                now = time.monotonic()
                elapsed = now - last_publish
                if elapsed >= self.stream_update_interval:
                    if delayed_publish_task:
                        delayed_publish_task.cancel()
                        delayed_publish_task = None
                    await publish(force=False)
                    return
                if delayed_publish_task is None:
                    delay = self.stream_update_interval - elapsed
                    delayed_publish_task = asyncio.create_task(delayed_publish(delay))

        try:
            result = await self.service.execute(user_id=user_id, command=command, on_stream_chunk=on_stream)
            async with lock:
                if delayed_publish_task:
                    delayed_publish_task.cancel()
                    delayed_publish_task = None
                await publish(force=True)
        except SessionUnavailableError:
            await message.answer("ℹ️ No active SSH session. Use /connect first.")
            return
        except Exception as exc:
            async with lock:
                if delayed_publish_task:
                    delayed_publish_task.cancel()
                    delayed_publish_task = None
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
        shell_state: dict,
    ) -> tuple[
        StreamChunkCallback,
        Callable[[], Awaitable[None]],
        Callable[[], tuple[str, bool]],
        Callable[[], Awaitable[None]],
    ]:
        stream_ctx = {
            "stream_ids": [self.stream.generate_stream_id()],
            "buffer": "",
            "lock": asyncio.Lock(),
            "last_publish": 0.0,
            "has_streamed": False,
            "delayed_publish_task": None,
            "last_pages": [],
        }
        shell_state["stream_ctx"] = stream_ctx

        async def publish(force: bool = False) -> None:
            if not stream_ctx["buffer"].strip():
                return
            now = time.monotonic()
            if not force and now - stream_ctx["last_publish"] < self.stream_update_interval:
                return
            stream_ctx["last_publish"] = now
            pages = self._format_stream_pages(stream_ctx["buffer"])
            for index, rendered in enumerate(pages):
                if index >= len(stream_ctx["stream_ids"]):
                    stream_ctx["stream_ids"].append(self.stream.generate_stream_id())
                if index < len(stream_ctx["last_pages"]) and stream_ctx["last_pages"][index] == rendered:
                    continue
                await self.stream.publish(
                    chat_id=chat_id,
                    stream_id=stream_ctx["stream_ids"][index],
                    text=rendered,
                    parse_mode="HTML",
                )
            stream_ctx["last_pages"] = pages

        async def delayed_publish(delay: float) -> None:
            try:
                await asyncio.sleep(delay)
                async with stream_ctx["lock"]:
                    await publish(force=True)
            except asyncio.CancelledError:
                return
            finally:
                stream_ctx["delayed_publish_task"] = None

        async def on_stream(chunk: str) -> None:
            async with stream_ctx["lock"]:
                if chunk:
                    stream_ctx["buffer"] += chunk
                    stream_ctx["has_streamed"] = True
                elapsed = time.monotonic() - stream_ctx["last_publish"]
                if elapsed >= self.stream_update_interval:
                    delayed_task = stream_ctx["delayed_publish_task"]
                    if delayed_task:
                        delayed_task.cancel()
                        stream_ctx["delayed_publish_task"] = None
                    await publish(force=False)
                    return
                if stream_ctx["delayed_publish_task"] is None:
                    delay = self.stream_update_interval - elapsed
                    stream_ctx["delayed_publish_task"] = asyncio.create_task(delayed_publish(delay))

        async def flush() -> None:
            async with stream_ctx["lock"]:
                delayed_task = stream_ctx["delayed_publish_task"]
                if delayed_task:
                    delayed_task.cancel()
                    stream_ctx["delayed_publish_task"] = None
                await publish(force=True)

        def get_stream_meta() -> tuple[str, bool]:
            return stream_ctx["buffer"], bool(stream_ctx["has_streamed"])

        async def rotate_stream() -> None:
            async with stream_ctx["lock"]:
                delayed_task = stream_ctx["delayed_publish_task"]
                if delayed_task:
                    delayed_task.cancel()
                    stream_ctx["delayed_publish_task"] = None
                stream_ctx["stream_ids"] = [self.stream.generate_stream_id()]
                stream_ctx["buffer"] = ""
                stream_ctx["last_publish"] = 0.0
                stream_ctx["last_pages"] = []

        return on_stream, flush, get_stream_meta, rotate_stream

    @staticmethod
    def _format_stream_pages(raw_output: str) -> list[str]:
        cleaned = Formatter.clean_terminal_output(raw_output)
        prefix = '<pre><code class="language-bash">'
        suffix = "</code></pre>"
        max_content_length = MAX_MESSAGE_LENGTH - len(prefix) - len(suffix)

        if max_content_length <= 0:
            return [Formatter.format_bash(cleaned)]

        pages: list[str] = []
        current: list[str] = []
        current_length = 0

        for char in cleaned:
            escaped = html.escape(char, quote=False)
            escaped_length = len(escaped)
            if current and current_length + escaped_length > max_content_length:
                pages.append(prefix + "".join(current) + suffix)
                current = [escaped]
                current_length = escaped_length
            else:
                current.append(escaped)
                current_length += escaped_length

        if current:
            pages.append(prefix + "".join(current) + suffix)

        return pages or [prefix + suffix]

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

    async def cmd_enter(self, message: Message) -> None:
        user_id = message.from_user.id
        try:
            await self.service.shell_input(user_id=user_id, text="")
        except SessionUnavailableError:
            await message.answer("ℹ️ Not in interactive mode.")
            return
        except Exception as exc:
            await message.answer(f"❌ Shell error: {Formatter.escape_html(str(exc))}")
            return

        shell_state = self._shell_state.get(user_id)
        if not shell_state:
            return
        rotate_stream = shell_state.get("rotate_stream")
        if rotate_stream:
            await rotate_stream()

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
                rotate_stream = shell_state.get("rotate_stream")
                if rotate_stream:
                    await rotate_stream()
                return

            async with shell_lock:
                if self._looks_interactive_terminal_app(text):
                    await message.answer(
                        "⚠️ Full-screen interactive programs are not supported in Telegram shell "
                        "(e.g. vim/top/less)."
                    )
                    return

                on_stream, flush_stream, get_stream_meta, rotate_stream = self._build_stream_callbacks(
                    chat_id=message.chat.id,
                    shell_state=shell_state,
                )
                shell_state["rotate_stream"] = rotate_stream
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
                finally:
                    shell_state.pop("rotate_stream", None)
                    shell_state.pop("stream_ctx", None)

                _, has_streamed = get_stream_meta()
                if result.output.strip() and not has_streamed:
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
