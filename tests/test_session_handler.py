from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.application.services import ShellCommandResult
from app.interfaces.telegram.handlers.session import SessionHandler


class _FakeStreamPublisher:
    def __init__(self) -> None:
        self._stream_id = 0

    def generate_stream_id(self) -> int:
        self._stream_id += 1
        return self._stream_id

    async def publish(self, chat_id: int, stream_id: int, text: str, parse_mode: str = "HTML") -> bool:
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

    async def shell_execute(self, user_id: int, command: str) -> ShellCommandResult:
        self.shell_commands.append(command)
        if command == "pwd":
            return ShellCommandResult(output="/srv/app", exit_code=0, cwd="/srv/app")
        return ShellCommandResult(output=f"ran:{command}", exit_code=0, cwd="/srv/app")

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
    handler = SessionHandler(
        service=service,
        stream_publisher=_FakeStreamPublisher(),
        stream_update_interval=0.2,
    )
    message = _FakeMessage("//usr/bin/id")

    await handler.handle_message(message)

    assert service.shell_commands == ["/usr/bin/id"]
    assert any("ran:/usr/bin/id" in item for item in message.answers)


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

    assert service.shell_commands == ["pwd"]
    assert "/srv/app" in message.answers[-1]
