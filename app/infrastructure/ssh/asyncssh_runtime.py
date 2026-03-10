import asyncio
import logging
import re
import shlex
import time
import uuid
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
        self.default_cwd: str = ""

        self._last_activity = time.monotonic()
        self._shell_process: asyncssh.SSHClientProcess | None = None
        self._shell_reader_task: asyncio.Task | None = None
        self._shell_callback: OutputCallback | None = None
        self.is_interactive = False
        self._probe_lock = asyncio.Lock()
        self._probe_begin_marker: str | None = None
        self._probe_end_marker: str | None = None
        self._probe_buffer: str = ""
        self._probe_future: asyncio.Future[str] | None = None
        self._command_lock = asyncio.Lock()
        self._command_begin_marker: str | None = None
        self._command_end_marker: str | None = None
        self._command_text: str = ""
        self._command_buffer: str = ""
        self._command_future: asyncio.Future[tuple[str, int, str]] | None = None

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
        default_cwd: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.default_cwd = (default_cwd or "").strip()

        connect_kwargs: dict = {
            "host": host,
            "port": port,
            "username": username,
            "known_hosts": None,
        }

        if key_data:
            self.auth_type = "key"
            imported_key = asyncssh.import_private_key(key_data.decode(), passphrase=password)
            connect_kwargs["client_keys"] = [imported_key]
            connect_kwargs["password"] = None
            self.key_cache = key_data
            self.password_cache = password or ""
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

        prepared_command = command
        if self.default_cwd:
            prepared_command = f"cd {shlex.quote(self.default_cwd)} && {command}"

        self._last_activity = time.monotonic()
        process = await self.conn.create_process(
            prepared_command,
            term_type="xterm",
            term_size=(200, 50),
        )

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
        if self.default_cwd:
            self._shell_process.stdin.write(f"cd {shlex.quote(self.default_cwd)}\n")
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
                    if self._probe_future:
                        self._probe_buffer += chunk
                        self._try_finish_probe()
                        continue
                    if self._command_future:
                        self._command_buffer += chunk
                        self._try_finish_command()
                        continue
                    await self._shell_callback(chunk)
                except asyncio.TimeoutError:
                    continue
                except asyncssh.misc.DisconnectError:
                    break
        except asyncio.CancelledError:
            pass

    def _try_finish_probe(self) -> None:
        if (
            not self._probe_future
            or not self._probe_begin_marker
            or not self._probe_end_marker
            or self._probe_future.done()
        ):
            return

        lines = [line.strip() for line in self._probe_buffer.replace("\r", "\n").split("\n")]

        try:
            begin_idx = next(index for index, line in enumerate(lines) if line == self._probe_begin_marker)
            end_idx = next(
                index
                for index, line in enumerate(lines[begin_idx + 1 :], start=begin_idx + 1)
                if line == self._probe_end_marker
            )
        except StopIteration:
            return

        cwd_line = ""
        for line in lines[begin_idx + 1 : end_idx]:
            if line:
                cwd_line = line
                break

        if cwd_line:
            self._probe_future.set_result(cwd_line)
        else:
            self._probe_future.set_exception(RuntimeError("Could not determine shell working directory"))

    def _try_finish_command(self) -> None:
        if (
            not self._command_future
            or not self._command_begin_marker
            or not self._command_end_marker
            or self._command_future.done()
        ):
            return

        begin_idx = self._command_buffer.find(self._command_begin_marker)
        if begin_idx == -1:
            return

        begin_line_end = self._command_buffer.find("\n", begin_idx)
        if begin_line_end == -1:
            return

        tail = self._command_buffer[begin_line_end + 1 :]
        marker_pattern = re.compile(
            rf"{re.escape(self._command_end_marker)}\|(-?\d+)\|([^\r\n]+)"
        )
        match = marker_pattern.search(tail)
        if not match:
            return

        output = tail[: match.start()]
        exit_code = int(match.group(1))
        cwd = match.group(2).strip()

        # Shell may echo the command line itself; remove a leading echoed line when present.
        lines = output.replace("\r", "\n").split("\n")
        cleaned_lines: list[str] = []
        skipped_echo = False
        for line in lines:
            stripped = line.strip()
            if not skipped_echo and stripped == self._command_text.strip():
                skipped_echo = True
                continue
            cleaned_lines.append(line)
        cleaned_output = "\n".join(cleaned_lines).strip("\n")

        self._command_future.set_result((cleaned_output, exit_code, cwd))

    async def get_shell_cwd(self) -> str:
        if not self._shell_process or not self.is_interactive:
            raise RuntimeError("No interactive shell active")

        async with self._probe_lock:
            if self._probe_future and not self._probe_future.done():
                raise RuntimeError("Shell probe already in progress")

            probe_id = uuid.uuid4().hex
            self._probe_begin_marker = f"__SSHBOT_PWD_BEGIN_{probe_id}__"
            self._probe_end_marker = f"__SSHBOT_PWD_END_{probe_id}__"
            self._probe_buffer = ""
            self._probe_future = asyncio.get_running_loop().create_future()

            self._shell_process.stdin.write(
                f"echo {self._probe_begin_marker}; pwd; echo {self._probe_end_marker}\n"
            )

            try:
                cwd = await asyncio.wait_for(self._probe_future, timeout=4.0)
                self._last_activity = time.monotonic()
                return cwd
            except asyncio.TimeoutError as exc:
                raise RuntimeError("Timed out while resolving shell working directory") from exc
            finally:
                self._probe_begin_marker = None
                self._probe_end_marker = None
                self._probe_buffer = ""
                self._probe_future = None

    async def run_shell_command(self, command: str) -> tuple[str, int, str]:
        if not self._shell_process or not self.is_interactive:
            raise RuntimeError("No interactive shell active")

        async with self._command_lock:
            if self._probe_future and not self._probe_future.done():
                raise RuntimeError("Shell probe already in progress")
            if self._command_future and not self._command_future.done():
                raise RuntimeError("Shell command already in progress")

            command_id = uuid.uuid4().hex
            self._command_begin_marker = f"__SSHBOT_CMD_BEGIN_{command_id}__"
            self._command_end_marker = f"__SSHBOT_CMD_END_{command_id}__"
            self._command_text = command
            self._command_buffer = ""
            self._command_future = asyncio.get_running_loop().create_future()

            self._shell_process.stdin.write(f"printf '%s\\n' '{self._command_begin_marker}'\n")
            self._shell_process.stdin.write(f"{command}\n")
            self._shell_process.stdin.write(
                "__sshbot_status=$?; "
                f"printf '%s|%s|%s\\n' '{self._command_end_marker}' "
                '"$__sshbot_status" "$PWD"\n'
            )

            try:
                result = await asyncio.wait_for(self._command_future, timeout=45.0)
                self._last_activity = time.monotonic()
                return result
            except asyncio.TimeoutError as exc:
                raise RuntimeError("Command timed out in interactive shell") from exc
            finally:
                self._command_begin_marker = None
                self._command_end_marker = None
                self._command_text = ""
                self._command_buffer = ""
                self._command_future = None

    async def interrupt_shell_command(self) -> None:
        if not self._shell_process or not self.is_interactive:
            raise RuntimeError("No interactive shell active")
        self._last_activity = time.monotonic()
        self._shell_process.stdin.write("\x03")

    async def send_to_shell(self, text: str) -> None:
        if not self._shell_process or not self.is_interactive:
            raise RuntimeError("No interactive shell active")
        self._last_activity = time.monotonic()
        self._shell_process.stdin.write(text + "\n")

    async def close_shell(self) -> None:
        self.is_interactive = False

        if self._probe_future and not self._probe_future.done():
            self._probe_future.set_exception(RuntimeError("Interactive shell closed"))
        self._probe_begin_marker = None
        self._probe_end_marker = None
        self._probe_buffer = ""
        self._probe_future = None
        if self._command_future and not self._command_future.done():
            self._command_future.set_exception(RuntimeError("Interactive shell closed"))
        self._command_begin_marker = None
        self._command_end_marker = None
        self._command_text = ""
        self._command_buffer = ""
        self._command_future = None

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
