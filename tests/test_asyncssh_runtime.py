import pytest

from app.infrastructure.ssh import asyncssh_runtime


class _FakeConnection:
    def is_closed(self) -> bool:
        return False


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
