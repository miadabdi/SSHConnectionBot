import asyncio

import pytest

from app.infrastructure.ssh import asyncssh_runtime


class _FakeStdin:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, value: str) -> None:
        self.writes.append(value)


class _FakeShellProcess:
    def __init__(self) -> None:
        self.stdin = _FakeStdin()


class _FakeConnection:
    def is_closed(self) -> bool:
        return False


class _FakeReader:
    async def read(self, size: int) -> str:
        return ""


class _FakeExecProcess:
    def __init__(self) -> None:
        self.stdout = _FakeReader()
        self.stderr = _FakeReader()
        self.exit_status = 0

    async def wait(self) -> None:
        return None


@pytest.mark.asyncio
async def test_connect_uses_passphrase_when_importing_private_key(monkeypatch: pytest.MonkeyPatch) -> None:
    imported_calls: list[tuple[str, str | None]] = []
    connect_kwargs: dict = {}

    def fake_import_private_key(data: str, passphrase: str | None = None) -> str:
        imported_calls.append((data, passphrase))
        return "imported-key"

    async def fake_connect(**kwargs):
        connect_kwargs.update(kwargs)
        return _FakeConnection()

    monkeypatch.setattr(asyncssh_runtime.asyncssh, "import_private_key", fake_import_private_key)
    monkeypatch.setattr(asyncssh_runtime.asyncssh, "connect", fake_connect)

    session = asyncssh_runtime.AsyncSSHSession(user_id=1, name="prod")
    await session.connect(
        host="example.com",
        port=22,
        username="root",
        password="my-passphrase",
        key_data=b"PRIVATE KEY DATA",
    )

    assert imported_calls == [("PRIVATE KEY DATA", "my-passphrase")]
    assert connect_kwargs["client_keys"] == ["imported-key"]
    assert connect_kwargs["password"] is None
    assert session.password_cache == "my-passphrase"
    assert session.auth_type == "key"


@pytest.mark.asyncio
async def test_connect_without_passphrase_still_imports_private_key(monkeypatch: pytest.MonkeyPatch) -> None:
    imported_calls: list[tuple[str, str | None]] = []

    def fake_import_private_key(data: str, passphrase: str | None = None) -> str:
        imported_calls.append((data, passphrase))
        return "imported-key"

    async def fake_connect(**kwargs):
        return _FakeConnection()

    monkeypatch.setattr(asyncssh_runtime.asyncssh, "import_private_key", fake_import_private_key)
    monkeypatch.setattr(asyncssh_runtime.asyncssh, "connect", fake_connect)

    session = asyncssh_runtime.AsyncSSHSession(user_id=2, name="staging")
    await session.connect(
        host="example.org",
        port=22,
        username="ubuntu",
        key_data=b"PRIVATE KEY DATA",
    )

    assert imported_calls == [("PRIVATE KEY DATA", None)]
    assert session.password_cache == ""


@pytest.mark.asyncio
async def test_execute_uses_default_cwd_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    class _FakeExecConnection(_FakeConnection):
        async def create_process(self, command: str, term_type: str, term_size: tuple[int, int]):
            captured["command"] = command
            return _FakeExecProcess()

    async def fake_connect(**kwargs):
        return _FakeExecConnection()

    monkeypatch.setattr(asyncssh_runtime.asyncssh, "connect", fake_connect)

    session = asyncssh_runtime.AsyncSSHSession(user_id=4, name="cwd")
    await session.connect(
        host="example.net",
        port=22,
        username="ubuntu",
        password="pw",
        default_cwd="/srv/project",
    )

    async def on_output(chunk: str) -> None:
        return None

    await session.execute("ls -la", on_output)

    assert captured["command"] == "cd /srv/project && ls -la"


@pytest.mark.asyncio
async def test_get_shell_cwd_from_active_shell_probe() -> None:
    session = asyncssh_runtime.AsyncSSHSession(user_id=3, name="main")
    session.is_interactive = True
    session._shell_process = _FakeShellProcess()

    task = asyncio.create_task(session.get_shell_cwd())
    await asyncio.sleep(0)

    begin = session._probe_begin_marker
    end = session._probe_end_marker
    assert begin is not None
    assert end is not None
    session._probe_buffer = f"{begin}\n/home/miad/project\n{end}\n"
    session._try_finish_probe()

    cwd = await task

    assert cwd == "/home/miad/project"
    assert session._shell_process.stdin.writes
