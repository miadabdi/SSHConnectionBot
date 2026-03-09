import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import asyncssh

logger = logging.getLogger(__name__)

SSH_CONNECT_TIMEOUT = 15
OutputCallback = Callable[[str], Awaitable[None]]


class AsyncSSHSession:
    def __init__(self, user_id: int, name: str) -> None:
        self.user_id = user_id
        self.name = name
        self.conn: asyncssh.SSHClientConnection | None = None
        self.host = ""
        self.port = 22
        self.username = ""
        self.auth_type: str = "password"

        self._last_activity = time.monotonic()
        self._shell_process: asyncssh.SSHClientProcess | None = None
        self._shell_reader_task: asyncio.Task | None = None
        self._shell_callback: OutputCallback | None = None
        self.is_interactive = False

        self.password_cache: str = ""
        self.key_cache: bytes = b""

    @property
    def last_activity(self) -> float:
        return self._last_activity

    @property
    def is_connected(self) -> bool:
        return self.conn is not None and not self.conn.is_closed()

    async def connect(
        self,
        host: str,
        port: int,
        username: str,
        password: str | None = None,
        key_data: bytes | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username

        connect_kwargs: dict = {
            "host": host,
            "port": port,
            "username": username,
            "known_hosts": None,
        }

        if key_data:
            self.auth_type = "key"
            imported_key = asyncssh.import_private_key(key_data.decode())
            connect_kwargs["client_keys"] = [imported_key]
            connect_kwargs["password"] = None
            self.key_cache = key_data
            self.password_cache = ""
        else:
            self.auth_type = "password"
            connect_kwargs["password"] = password
            connect_kwargs["client_keys"] = None
            self.password_cache = password or ""
            self.key_cache = b""

        self.conn = await asyncio.wait_for(asyncssh.connect(**connect_kwargs), timeout=SSH_CONNECT_TIMEOUT)
        self._last_activity = time.monotonic()

    async def execute(self, command: str, on_output_chunk: OutputCallback) -> int:
        if not self.conn:
            raise RuntimeError("Not connected")

        self._last_activity = time.monotonic()
        process = await self.conn.create_process(command, term_type="xterm", term_size=(200, 50))

        async def read_stream(stream: asyncssh.SSHReader) -> None:
            while True:
                try:
                    chunk = await asyncio.wait_for(stream.read(4096), timeout=0.5)
                    if not chunk:
                        break
                    await on_output_chunk(chunk)
                except asyncio.TimeoutError:
                    if process.exit_status is not None:
                        tail = await stream.read(4096)
                        if tail:
                            await on_output_chunk(tail)
                        break
                except asyncssh.misc.DisconnectError:
                    break

        await asyncio.gather(read_stream(process.stdout), read_stream(process.stderr))
        await process.wait()
        self._last_activity = time.monotonic()

        if process.exit_status is None:
            return -1
        return process.exit_status

    async def open_shell(self, on_output_chunk: OutputCallback) -> None:
        if not self.conn:
            raise RuntimeError("Not connected")
        if self.is_interactive:
            raise RuntimeError("Interactive shell already open")

        self._shell_callback = on_output_chunk
        self._shell_process = await self.conn.create_process(term_type="xterm", term_size=(200, 50))
        self.is_interactive = True
        self._last_activity = time.monotonic()
        self._shell_reader_task = asyncio.create_task(self._read_shell_loop())

    async def _read_shell_loop(self) -> None:
        if not self._shell_process or not self._shell_callback:
            return

        try:
            while self.is_interactive:
                try:
                    chunk = await asyncio.wait_for(self._shell_process.stdout.read(4096), timeout=1.0)
                    if not chunk:
                        break
                    self._last_activity = time.monotonic()
                    await self._shell_callback(chunk)
                except asyncio.TimeoutError:
                    continue
                except asyncssh.misc.DisconnectError:
                    break
        except asyncio.CancelledError:
            pass

    async def send_to_shell(self, text: str) -> None:
        if not self._shell_process or not self.is_interactive:
            raise RuntimeError("No interactive shell active")
        self._last_activity = time.monotonic()
        self._shell_process.stdin.write(text + "\n")

    async def close_shell(self) -> None:
        self.is_interactive = False

        if self._shell_reader_task:
            self._shell_reader_task.cancel()
            try:
                await self._shell_reader_task
            except asyncio.CancelledError:
                pass
            self._shell_reader_task = None

        if self._shell_process:
            self._shell_process.stdin.write("exit\n")
            try:
                await asyncio.wait_for(self._shell_process.wait(), timeout=3)
            except Exception:
                pass
            self._shell_process = None

        self._shell_callback = None

    async def sftp_download(self, remote_path: str, local_path: str) -> None:
        if not self.conn:
            raise RuntimeError("Not connected")
        self._last_activity = time.monotonic()
        async with self.conn.start_sftp_client() as sftp:
            await sftp.get(remote_path, local_path)

    async def sftp_upload(self, local_path: str, remote_path: str) -> None:
        if not self.conn:
            raise RuntimeError("Not connected")
        self._last_activity = time.monotonic()
        async with self.conn.start_sftp_client() as sftp:
            await sftp.put(local_path, remote_path)

    async def disconnect(self) -> None:
        if self.is_interactive:
            await self.close_shell()
        if self.conn:
            self.conn.close()
            await self.conn.wait_closed()
            self.conn = None


class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[int, dict[str, AsyncSSHSession]] = {}
        self._active: dict[int, str] = {}

    def create_session(self, user_id: int, name: str) -> AsyncSSHSession:
        return AsyncSSHSession(user_id=user_id, name=name)

    def store(self, user_id: int, session: AsyncSSHSession) -> None:
        self._sessions.setdefault(user_id, {})[session.name] = session
        self._active[user_id] = session.name

    def get(self, user_id: int, name: str | None = None) -> AsyncSSHSession | None:
        user_sessions = self._sessions.get(user_id, {})
        target = name or self._active.get(user_id)
        if not target:
            return None

        session = user_sessions.get(target)
        if session and session.is_connected:
            return session

        if target in user_sessions:
            user_sessions.pop(target, None)
        if self._active.get(user_id) == target:
            self._active.pop(user_id, None)
            if user_sessions:
                self._active[user_id] = next(iter(user_sessions))
        return None

    def get_active(self, user_id: int) -> AsyncSSHSession | None:
        return self.get(user_id)

    def get_active_name(self, user_id: int) -> str | None:
        return self._active.get(user_id)

    def get_all(self, user_id: int) -> dict[str, AsyncSSHSession]:
        sessions = self._sessions.get(user_id, {})
        alive = {name: s for name, s in sessions.items() if s.is_connected}
        self._sessions[user_id] = alive
        if not alive:
            self._active.pop(user_id, None)
        elif self._active.get(user_id) not in alive:
            self._active[user_id] = next(iter(alive))
        return alive

    def switch(self, user_id: int, name: str) -> AsyncSSHSession | None:
        session = self.get(user_id, name)
        if session:
            self._active[user_id] = name
        return session

    async def remove(self, user_id: int, name: str | None = None) -> None:
        sessions = self._sessions.get(user_id, {})
        target = name or self._active.get(user_id)
        if not target:
            return

        session = sessions.pop(target, None)
        if session:
            await session.disconnect()

        if self._active.get(user_id) == target:
            if sessions:
                self._active[user_id] = next(iter(sessions))
            else:
                self._active.pop(user_id, None)

    async def remove_all(self, user_id: int) -> None:
        sessions = self._sessions.pop(user_id, {})
        for session in sessions.values():
            await session.disconnect()
        self._active.pop(user_id, None)

    def check_timeouts(self, timeout_minutes: int) -> list[tuple[int, str]]:
        cutoff = time.monotonic() - timeout_minutes * 60
        timed_out: list[tuple[int, str]] = []
        for user_id, sessions in self._sessions.items():
            for name, session in sessions.items():
                if session.last_activity < cutoff:
                    timed_out.append((user_id, name))
        return timed_out

    async def close_all(self) -> None:
        for user_id in list(self._sessions.keys()):
            await self.remove_all(user_id)
