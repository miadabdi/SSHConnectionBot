from dataclasses import dataclass

import pytest

from app.application.services import ConnectionService
from app.domain.entities import SavedServer
from app.domain.errors import NotFoundError


class _FakeSession:
    def __init__(self, user_id: int, name: str) -> None:
        self.user_id = user_id
        self.name = name
        self.host = ""
        self.port = 22
        self.username = ""
        self.auth_type = "password"
        self.is_interactive = False
        self.default_cwd = ""

    async def connect(
        self,
        host: str,
        port: int,
        username: str,
        password: str | None = None,
        key_data: bytes | None = None,
        default_cwd: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.auth_type = "key" if key_data else "password"
        self.default_cwd = default_cwd or ""


class _FakeSessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[int, dict[str, _FakeSession]] = {}
        self._active: dict[int, str] = {}

    def create_session(self, user_id: int, name: str) -> _FakeSession:
        return _FakeSession(user_id=user_id, name=name)

    def store(self, user_id: int, session: _FakeSession) -> None:
        self._sessions.setdefault(user_id, {})[session.name] = session
        self._active[user_id] = session.name

    def get(self, user_id: int, name: str | None = None) -> _FakeSession | None:
        target_name = name or self._active.get(user_id)
        if not target_name:
            return None
        return self._sessions.get(user_id, {}).get(target_name)

    def get_all(self, user_id: int) -> dict[str, _FakeSession]:
        return dict(self._sessions.get(user_id, {}))

    def get_active(self, user_id: int) -> _FakeSession | None:
        return self.get(user_id)

    def get_active_name(self, user_id: int) -> str | None:
        return self._active.get(user_id)

    def switch(self, user_id: int, name: str) -> _FakeSession | None:
        session = self.get(user_id, name)
        if session:
            self._active[user_id] = name
        return session

    async def remove(self, user_id: int, name: str | None = None) -> None:
        target_name = name or self._active.get(user_id)
        if not target_name:
            return
        self._sessions.get(user_id, {}).pop(target_name, None)

    async def remove_all(self, user_id: int) -> None:
        self._sessions.pop(user_id, None)

    async def close_all(self) -> None:
        self._sessions.clear()

    def check_timeouts(self, timeout_minutes: int) -> list[tuple[int, str]]:
        return []


class _FakeHistoryRepo:
    def __init__(self) -> None:
        self.saved_session_names: list[str] = []

    async def save_session(
        self,
        user_id: int,
        host: str,
        port: int,
        username: str,
        session_name: str,
        auth_type: str,
        encrypted_password: str,
        encrypted_key: str,
    ) -> str:
        self.saved_session_names.append(session_name)
        return session_name

    async def deactivate_sessions(self, user_id: int, session_name: str | None = None) -> None:
        return None

    async def get_recent_sessions(self, user_id: int, limit: int = 10):
        return []

    async def get_active_session(self, user_id: int, session_name: str | None = None):
        return None


@dataclass
class _FakeServerRepo:
    server: SavedServer | None

    async def get_server(self, user_id: int, name: str) -> SavedServer | None:
        if not self.server or self.server.user_id != user_id or self.server.name != name:
            return None
        return self.server

    async def upsert_server(self, server: SavedServer) -> None:
        self.server = server

    async def list_servers(self, user_id: int) -> list[SavedServer]:
        if self.server and self.server.user_id == user_id:
            return [self.server]
        return []

    async def delete_server(self, user_id: int, name: str) -> bool:
        return False

    async def update_server_group(self, user_id: int, server_name: str, group: str) -> bool:
        return False

    async def list_servers_by_group(self, user_id: int, group: str) -> list[SavedServer]:
        return []


class _FakeCipher:
    def encrypt(self, value: str) -> str:
        return value

    def decrypt(self, value: str) -> str:
        return value


@pytest.mark.asyncio
async def test_quick_connect_uses_incremental_session_names() -> None:
    registry = _FakeSessionRegistry()
    history = _FakeHistoryRepo()
    repo = _FakeServerRepo(
        server=SavedServer(
            user_id=1,
            name="main",
            host="example.com",
            port=22,
            username="root",
            auth_type="password",
            group="",
            encrypted_password="pw",
            encrypted_key="",
            default_cwd="/srv/main",
        )
    )
    service = ConnectionService(
        sessions=registry,
        history_repo=history,
        server_repo=repo,
        cipher=_FakeCipher(),
    )

    first = await service.quick_connect(user_id=1, name="main")
    second = await service.quick_connect(user_id=1, name="main")
    third = await service.quick_connect(user_id=1, name="main")

    assert first.name == "main"
    assert second.name == "main-2"
    assert third.name == "main-3"
    assert first.default_cwd == "/srv/main"
    assert second.default_cwd == "/srv/main"
    assert history.saved_session_names == ["main", "main-2", "main-3"]


@pytest.mark.asyncio
async def test_quick_connect_raises_when_saved_server_missing() -> None:
    service = ConnectionService(
        sessions=_FakeSessionRegistry(),
        history_repo=_FakeHistoryRepo(),
        server_repo=_FakeServerRepo(server=None),
        cipher=_FakeCipher(),
    )

    with pytest.raises(NotFoundError):
        await service.quick_connect(user_id=1, name="missing")
