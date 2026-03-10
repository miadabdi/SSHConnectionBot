import pytest

from app.application.services import FileTransferService
from app.domain.errors import SessionUnavailableError, ValidationError


class _FakeSession:
    def __init__(self, interactive: bool, cwd: str = "/home/ubuntu") -> None:
        self.is_interactive = interactive
        self.cwd = cwd
        self.upload_calls: list[tuple[str, str]] = []

    async def get_shell_cwd(self) -> str:
        return self.cwd

    async def sftp_upload(self, local_path: str, remote_path: str) -> None:
        self.upload_calls.append((local_path, remote_path))


class _FakeSessions:
    def __init__(self, session: _FakeSession | None) -> None:
        self.session = session

    def get_active(self, user_id: int) -> _FakeSession | None:
        return self.session


@pytest.mark.asyncio
async def test_upload_with_explicit_remote_path() -> None:
    session = _FakeSession(interactive=False)
    service = FileTransferService(sessions=_FakeSessions(session))

    uploaded_path = await service.upload_local_file(
        user_id=1,
        local_path="/tmp/archive.tar.gz",
        remote_path="/var/backups/archive.tar.gz",
    )

    assert uploaded_path == "/var/backups/archive.tar.gz"
    assert session.upload_calls == [("/tmp/archive.tar.gz", "/var/backups/archive.tar.gz")]


@pytest.mark.asyncio
async def test_upload_without_remote_path_uses_shell_cwd() -> None:
    session = _FakeSession(interactive=True, cwd="/opt/data")
    service = FileTransferService(sessions=_FakeSessions(session))

    uploaded_path = await service.upload_local_file(
        user_id=1,
        local_path="/tmp/report.txt",
        remote_path=None,
    )

    assert uploaded_path == "/opt/data/report.txt"
    assert session.upload_calls == [("/tmp/report.txt", "/opt/data/report.txt")]


@pytest.mark.asyncio
async def test_upload_without_remote_path_requires_interactive_shell() -> None:
    session = _FakeSession(interactive=False)
    service = FileTransferService(sessions=_FakeSessions(session))

    with pytest.raises(ValidationError):
        await service.upload_local_file(user_id=1, local_path="/tmp/file.bin", remote_path=None)

    assert session.upload_calls == []


@pytest.mark.asyncio
async def test_upload_requires_active_session() -> None:
    service = FileTransferService(sessions=_FakeSessions(None))

    with pytest.raises(SessionUnavailableError):
        await service.upload_local_file(user_id=1, local_path="/tmp/file.bin", remote_path="/tmp/x")
