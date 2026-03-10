from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.application.services import ShellCommandResult
from app.interfaces.telegram.handlers.session import SessionHandler


class _FakeStreamPublisher:
    def __init__(self) -> None:
        self._stream_id = 0
        self.published: list[tuple[int, int, str, str]] = []

    def generate_stream_id(self) -> int:
        self._stream_id += 1
        return self._stream_id

    async def publish(self, chat_id: int, stream_id: int, text: str, parse_mode: str = "HTML") -> bool:
        self.published.append((chat_id, stream_id, text, parse_mode))
        return True


@dataclass
class _FakeSession:
    name: str = "main"
    is_interactive: bool = False


class _FakeSessions:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def get_active(self, user_id: int) -> _FakeSession | None:
        return self._session


class _FakeCommandService:
    def __init__(self, session: _FakeSession) -> None:
        self.sessions = _FakeSessions(session)
        self.shell_commands: list[str] = []
        self.interrupt_calls = 0
        self.prompt_mode = False

    async def shell_execute(self, user_id: int, command: str, on_stream_chunk=None) -> ShellCommandResult:
        self.shell_commands.append(command)
        if on_stream_chunk and command != "pwd":
            await on_stream_chunk(f"ran:{command}")
        if command == "pwd":
            return ShellCommandResult(output="/srv/app", exit_code=0, cwd="/srv/app")
        if self.prompt_mode and command == "sudo apt update":
            return ShellCommandResult(
                output="",
                exit_code=None,
                cwd="",
                done=False,
                prompt="[sudo] password for ubuntu:",
            )
        return ShellCommandResult(output=f"ran:{command}", exit_code=0, cwd="/srv/app")

    async def shell_reply(self, user_id: int, text: str, on_stream_chunk=None) -> ShellCommandResult:
        self.shell_commands.append(f"reply:{text}")
        if on_stream_chunk:
            await on_stream_chunk(f"reply-ran:{text}")
        return ShellCommandResult(output=f"reply-ran:{text}", exit_code=0, cwd="/srv/app")

    async def shell_get_cwd(self, user_id: int) -> str:
        return "/srv/app"

    async def shell_interrupt(self, user_id: int) -> None:
        self.interrupt_calls += 1


class _FakeMessage:
    def __init__(self, text: str, user_id: int = 10, chat_id: int = 99) -> None:
        self.text = text
        self.from_user = SimpleNamespace(id=user_id)
        self.chat = SimpleNamespace(id=chat_id)
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append(text)
        return self

    async def edit_text(self, text: str, **kwargs):
        self.answers.append(text)
        return self


@pytest.mark.asyncio
async def test_handle_message_blocks_single_slash_commands_non_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FakeCommandService(_FakeSession(is_interactive=False))
    handler = SessionHandler(
        service=service,
        stream_publisher=_FakeStreamPublisher(),
        stream_update_interval=0.2,
    )
    message = _FakeMessage("/unknown")
    called = {"value": False}

    async def fake_execute_command(message, command: str) -> None:
        called["value"] = True

    monkeypatch.setattr(handler, "execute_command", fake_execute_command)
    await handler.handle_message(message)

    assert called["value"] is False
    assert "reserved for bot commands" in message.answers[-1]


@pytest.mark.asyncio
async def test_handle_message_allows_double_slash_escape_in_interactive_mode() -> None:
    service = _FakeCommandService(_FakeSession(is_interactive=True))
    stream = _FakeStreamPublisher()
    handler = SessionHandler(
        service=service,
        stream_publisher=stream,
        stream_update_interval=0.2,
    )
    message = _FakeMessage("//usr/bin/id")

    await handler.handle_message(message)

    assert service.shell_commands == ["/usr/bin/id"]
    assert any("ran:/usr/bin/id" in item[2] for item in stream.published)


@pytest.mark.asyncio
async def test_handle_message_allows_double_slash_escape_in_command_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FakeCommandService(_FakeSession(is_interactive=False))
    handler = SessionHandler(
        service=service,
        stream_publisher=_FakeStreamPublisher(),
        stream_update_interval=0.2,
    )
    message = _FakeMessage("//bin/ls")
    captured: list[str] = []

    async def fake_execute_command(message, command: str) -> None:
        captured.append(command)

    monkeypatch.setattr(handler, "execute_command", fake_execute_command)
    await handler.handle_message(message)

    assert captured == ["/bin/ls"]
    assert message.answers and "[main]" in message.answers[0]


@pytest.mark.asyncio
async def test_cmd_cancel_calls_shell_interrupt() -> None:
    service = _FakeCommandService(_FakeSession(is_interactive=True))
    handler = SessionHandler(
        service=service,
        stream_publisher=_FakeStreamPublisher(),
        stream_update_interval=0.2,
    )
    message = _FakeMessage("/cancel")

    await handler.cmd_cancel(message)

    assert service.interrupt_calls == 1
    assert "Ctrl+C" in message.answers[-1]


@pytest.mark.asyncio
async def test_cmd_pwd_returns_current_directory() -> None:
    service = _FakeCommandService(_FakeSession(is_interactive=True))
    handler = SessionHandler(
        service=service,
        stream_publisher=_FakeStreamPublisher(),
        stream_update_interval=0.2,
    )
    message = _FakeMessage("/pwd")

    await handler.cmd_pwd(message)

    assert service.shell_commands == []
    assert "/srv/app" in message.answers[-1]


@pytest.mark.asyncio
async def test_handle_message_interactive_prompt_then_reply() -> None:
    service = _FakeCommandService(_FakeSession(is_interactive=True))
    service.prompt_mode = True
    stream = _FakeStreamPublisher()
    handler = SessionHandler(
        service=service,
        stream_publisher=stream,
        stream_update_interval=0.2,
    )
    user_id = 10
    handler._shell_state[user_id] = {"session": "main", "awaiting_input": False}

    first = _FakeMessage("sudo apt update", user_id=user_id)
    await handler.handle_message(first)
    assert any("Input required" in line for line in first.answers)

    second = _FakeMessage("secret", user_id=user_id)
    await handler.handle_message(second)
    assert "reply:secret" in service.shell_commands
    assert any("reply-ran:secret" in item[2] for item in stream.published)


@pytest.mark.asyncio
async def test_handle_message_ignores_command_interrupted_error(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _FakeCommandService(_FakeSession(is_interactive=True))
    handler = SessionHandler(
        service=service,
        stream_publisher=_FakeStreamPublisher(),
        stream_update_interval=0.2,
    )
    message = _FakeMessage("ls")

    async def interrupted(*args, **kwargs):
        raise RuntimeError("Command interrupted")

    monkeypatch.setattr(service, "shell_execute", interrupted)
    await handler.handle_message(message)

    assert message.answers == []
