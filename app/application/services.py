import os
import posixpath
import re
import tempfile
from dataclasses import dataclass
from typing import Any

from app.domain.entities import CommandResult, Macro, SavedServer, ServerGroup, SSHSessionSnapshot
from app.domain.errors import (
    NotFoundError,
    SessionUnavailableError,
    ValidationError,
)
from app.domain.ports import (
    GroupRepository,
    MacroRepository,
    MonitorCollector,
    OutputCallback,
    SavedServerRepository,
    SecretCipher,
    SessionHistoryRepository,
    SessionRegistry,
)


@dataclass(slots=True)
class DisconnectResult:
    disconnected: bool
    name: str = ""
    host: str = ""
    port: int = 0


@dataclass(slots=True)
class ShellCommandResult:
    output: str
    exit_code: int | None
    cwd: str
    done: bool = True
    prompt: str = ""


class ConnectionService:
    def __init__(
        self,
        sessions: SessionRegistry,
        history_repo: SessionHistoryRepository,
        server_repo: SavedServerRepository,
        cipher: SecretCipher,
    ) -> None:
        self.sessions = sessions
        self.history_repo = history_repo
        self.server_repo = server_repo
        self.cipher = cipher

    async def connect(
        self,
        user_id: int,
        name: str,
        host: str,
        port: int,
        username: str,
        password: str | None = None,
        key_data: bytes | None = None,
    ):
        if self.sessions.get(user_id, name):
            raise ValidationError(f"Session '{name}' already exists")

        session = self.sessions.create_session(user_id=user_id, name=name)
        await session.connect(host=host, port=port, username=username, password=password, key_data=key_data)
        self.sessions.store(user_id=user_id, session=session)

        encrypted_password = self.cipher.encrypt(password) if password else ""
        encrypted_key = self.cipher.encrypt(key_data.decode()) if key_data else ""
        await self.history_repo.save_session(
            user_id=user_id,
            host=host,
            port=port,
            username=username,
            session_name=name,
            auth_type=session.auth_type,
            encrypted_password=encrypted_password,
            encrypted_key=encrypted_key,
        )
        return session

    async def quick_connect(self, user_id: int, name: str):
        saved = await self.server_repo.get_server(user_id=user_id, name=name)
        if not saved:
            raise NotFoundError(f"Saved server '{name}' not found")

        session_name = self._next_session_name(user_id=user_id, base_name=name)
        password = self.cipher.decrypt(saved.encrypted_password) if saved.encrypted_password else None
        key_data = self.cipher.decrypt(saved.encrypted_key).encode() if saved.encrypted_key else None

        session = self.sessions.create_session(user_id=user_id, name=session_name)
        await session.connect(
            host=saved.host,
            port=saved.port,
            username=saved.username,
            password=password,
            key_data=key_data,
            default_cwd=saved.default_cwd or None,
        )
        self.sessions.store(user_id=user_id, session=session)

        await self.history_repo.save_session(
            user_id=user_id,
            host=saved.host,
            port=saved.port,
            username=saved.username,
            session_name=session_name,
            auth_type=saved.auth_type,
            encrypted_password=saved.encrypted_password,
            encrypted_key=saved.encrypted_key,
        )
        return session

    def _next_session_name(self, user_id: int, base_name: str) -> str:
        active_names = set(self.sessions.get_all(user_id).keys())
        if base_name not in active_names:
            return base_name

        index = 2
        while True:
            candidate = f"{base_name}-{index}"
            if candidate not in active_names:
                return candidate
            index += 1

    async def disconnect(self, user_id: int, name: str | None = None) -> DisconnectResult:
        session = self.sessions.get(user_id, name)
        if not session:
            return DisconnectResult(disconnected=False)

        session_name = session.name
        host = session.host
        port = session.port
        await self.sessions.remove(user_id=user_id, name=session_name)
        await self.history_repo.deactivate_sessions(user_id=user_id, session_name=session_name)
        return DisconnectResult(disconnected=True, name=session_name, host=host, port=port)

    async def disconnect_all(self, user_id: int) -> int:
        active = self.sessions.get_all(user_id)
        count = len(active)
        if count:
            await self.sessions.remove_all(user_id)
            await self.history_repo.deactivate_sessions(user_id=user_id)
        return count

    def switch(self, user_id: int, name: str):
        session = self.sessions.switch(user_id=user_id, name=name)
        if not session:
            raise NotFoundError(f"Session '{name}' not found")
        return session

    def get_status(self, user_id: int) -> list[SSHSessionSnapshot]:
        result: list[SSHSessionSnapshot] = []
        all_sessions = self.sessions.get_all(user_id)
        for name, session in all_sessions.items():
            result.append(
                SSHSessionSnapshot(
                    user_id=user_id,
                    name=name,
                    host=session.host,
                    port=session.port,
                    username=session.username,
                    auth_type=session.auth_type,
                    is_interactive=session.is_interactive,
                )
            )
        return result

    def get_active_name(self, user_id: int) -> str | None:
        return self.sessions.get_active_name(user_id)

    async def get_history(self, user_id: int, limit: int = 10):
        return await self.history_repo.get_recent_sessions(user_id=user_id, limit=limit)

    async def timeout_cleanup(self, timeout_minutes: int) -> list[tuple[int, str, str, int]]:
        timed_out_sessions = self.sessions.check_timeouts(timeout_minutes)
        disconnected: list[tuple[int, str, str, int]] = []

        for user_id, session_name in timed_out_sessions:
            session = self.sessions.get(user_id, session_name)
            if not session:
                continue
            host = session.host
            port = session.port
            await self.sessions.remove(user_id, session_name)
            await self.history_repo.deactivate_sessions(user_id, session_name)
            disconnected.append((user_id, session_name, host, port))

        return disconnected


class CommandService:
    def __init__(self, sessions: SessionRegistry, history_repo: SessionHistoryRepository) -> None:
        self.sessions = sessions
        self.history_repo = history_repo

    async def execute(self, user_id: int, command: str, on_stream_chunk) -> CommandResult:
        session = self.sessions.get_active(user_id)
        if not session:
            raise SessionUnavailableError("No active session")

        chunks: list[str] = []

        async def fanout(chunk: str) -> None:
            chunks.append(chunk)
            await on_stream_chunk(chunk)

        try:
            exit_code = await session.execute(command=command, on_output_chunk=fanout)
        except Exception:
            if session and not session.is_connected:
                await self.sessions.remove(user_id, session.name)
                await self.history_repo.deactivate_sessions(user_id, session.name)
            raise

        return CommandResult(exit_code=exit_code, output="".join(chunks))

    async def enter_shell(self, user_id: int, on_stream_chunk) -> Any:
        session = self.sessions.get_active(user_id)
        if not session:
            raise SessionUnavailableError("No active session")
        await session.open_shell(on_stream_chunk)
        return session

    async def exit_shell(self, user_id: int):
        session = self.sessions.get_active(user_id)
        if not session or not session.is_interactive:
            raise SessionUnavailableError("No interactive session")
        await session.close_shell()
        return session

    async def shell_input(self, user_id: int, text: str) -> None:
        session = self.sessions.get_active(user_id)
        if not session or not session.is_interactive:
            raise SessionUnavailableError("No interactive session")
        await session.send_to_shell(text)

    async def shell_execute(
        self,
        user_id: int,
        command: str,
        on_stream_chunk: OutputCallback | None = None,
    ) -> ShellCommandResult:
        session = self.sessions.get_active(user_id)
        if not session or not session.is_interactive:
            raise SessionUnavailableError("No interactive session")

        output, exit_code, cwd = await session.run_shell_command(command, on_output_chunk=on_stream_chunk)
        return ShellCommandResult(output=output, exit_code=exit_code, cwd=cwd, done=True)

    async def shell_reply(
        self,
        user_id: int,
        text: str,
        on_stream_chunk: OutputCallback | None = None,
    ) -> ShellCommandResult:
        session = self.sessions.get_active(user_id)
        if not session or not session.is_interactive:
            raise SessionUnavailableError("No interactive session")

        output, exit_code, cwd = await session.reply_shell_prompt(text, on_output_chunk=on_stream_chunk)
        return ShellCommandResult(output=output, exit_code=exit_code, cwd=cwd, done=True)

    async def shell_get_cwd(self, user_id: int) -> str:
        session = self.sessions.get_active(user_id)
        if not session or not session.is_interactive:
            raise SessionUnavailableError("No interactive session")
        return await session.get_shell_cwd()

    async def shell_interrupt(self, user_id: int) -> None:
        session = self.sessions.get_active(user_id)
        if not session or not session.is_interactive:
            raise SessionUnavailableError("No interactive session")
        await session.interrupt_shell_command()


class SavedServerService:
    def __init__(
        self,
        sessions: SessionRegistry,
        history_repo: SessionHistoryRepository,
        server_repo: SavedServerRepository,
    ) -> None:
        self.sessions = sessions
        self.history_repo = history_repo
        self.server_repo = server_repo

    async def save_current_as(
        self,
        user_id: int,
        name: str,
        default_cwd: str | None = None,
    ) -> SavedServer:
        session = self.sessions.get_active(user_id)
        if not session:
            raise SessionUnavailableError("No active session")

        history = await self.history_repo.get_active_session(user_id=user_id, session_name=session.name)
        encrypted_password = history.encrypted_password if history else ""
        encrypted_key = history.encrypted_key if history else ""

        existing = await self.server_repo.get_server(user_id=user_id, name=name)
        group_name = existing.group if existing else ""
        resolved_cwd = (default_cwd or "").strip()

        if not resolved_cwd:
            if session.is_interactive:
                try:
                    resolved_cwd = (await session.get_shell_cwd()).strip()
                except Exception:
                    resolved_cwd = ""
            if not resolved_cwd and existing:
                resolved_cwd = existing.default_cwd

        server = SavedServer(
            user_id=user_id,
            name=name,
            host=session.host,
            port=session.port,
            username=session.username,
            auth_type=session.auth_type,
            group=group_name,
            encrypted_password=encrypted_password,
            encrypted_key=encrypted_key,
            default_cwd=resolved_cwd,
        )
        await self.server_repo.upsert_server(server)
        return server

    async def list_servers(self, user_id: int) -> list[SavedServer]:
        return await self.server_repo.list_servers(user_id)

    async def get_server(self, user_id: int, name: str) -> SavedServer:
        server = await self.server_repo.get_server(user_id, name)
        if not server:
            raise NotFoundError(f"Saved server '{name}' not found")
        return server

    async def delete_server(self, user_id: int, name: str) -> bool:
        return await self.server_repo.delete_server(user_id, name)


class GroupService:
    def __init__(self, group_repo: GroupRepository, server_repo: SavedServerRepository) -> None:
        self.group_repo = group_repo
        self.server_repo = server_repo

    async def upsert_and_assign(self, user_id: int, group_name: str, server_names: list[str]) -> tuple[list[str], list[str]]:
        await self.group_repo.upsert_group(ServerGroup(user_id=user_id, name=group_name))
        assigned: list[str] = []
        missing: list[str] = []

        for server_name in server_names:
            updated = await self.server_repo.update_server_group(user_id=user_id, server_name=server_name, group=group_name)
            if updated:
                assigned.append(server_name)
            else:
                missing.append(server_name)

        return assigned, missing

    async def list_groups_with_servers(self, user_id: int) -> dict[str, list[SavedServer]]:
        groups = await self.group_repo.list_groups(user_id)
        grouped: dict[str, list[SavedServer]] = {}
        for group in groups:
            grouped[group.name] = await self.server_repo.list_servers_by_group(user_id, group.name)
        return grouped

    async def delete_group(self, user_id: int, group_name: str) -> bool:
        return await self.group_repo.delete_group(user_id, group_name)


class MacroService:
    def __init__(self, macro_repo: MacroRepository) -> None:
        self.macro_repo = macro_repo

    async def save_macro(self, user_id: int, name: str, command: str) -> None:
        await self.macro_repo.upsert_macro(Macro(user_id=user_id, name=name, command=command))

    async def list_macros(self, user_id: int) -> list[Macro]:
        return await self.macro_repo.list_macros(user_id)

    async def get_macro(self, user_id: int, name: str) -> Macro:
        macro = await self.macro_repo.get_macro(user_id, name)
        if not macro:
            raise NotFoundError(f"Macro '{name}' not found")
        return macro

    async def delete_macro(self, user_id: int, name: str) -> bool:
        return await self.macro_repo.delete_macro(user_id, name)


class FileTransferService:
    def __init__(self, sessions: SessionRegistry) -> None:
        self.sessions = sessions

    async def download_to_temp_file(self, user_id: int, remote_path: str) -> tuple[str, str]:
        session = self.sessions.get_active(user_id)
        if not session:
            raise SessionUnavailableError("No active session")

        file_name = os.path.basename(remote_path) or "downloaded_file"
        temp_dir = tempfile.mkdtemp(prefix="sshbot-dl-")
        local_path = os.path.join(temp_dir, file_name)
        await session.sftp_download(remote_path=remote_path, local_path=local_path)
        return temp_dir, local_path

    async def upload_local_file(
        self,
        user_id: int,
        local_path: str,
        remote_path: str | None = None,
    ) -> str:
        session = self.sessions.get_active(user_id)
        if not session:
            raise SessionUnavailableError("No active session")

        target_path = remote_path
        if not target_path:
            if not session.is_interactive:
                raise ValidationError("Interactive shell is required when remote path is omitted")
            shell_cwd = await session.get_shell_cwd()
            file_name = os.path.basename(local_path) or "upload.bin"
            target_path = posixpath.join(shell_cwd, file_name)

        await session.sftp_upload(local_path=local_path, remote_path=target_path)
        return target_path


class MonitorService:
    def __init__(self, sessions: SessionRegistry, collector: MonitorCollector) -> None:
        self.sessions = sessions
        self.collector = collector

    async def monitor(self, user_id: int):
        session = self.sessions.get_active(user_id)
        if not session:
            raise SessionUnavailableError("No active session")
        return session, await self.collector.collect(session)


def parse_monitor_output(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    keys = ["os", "hostname", "uptime", "cpu_cores", "load", "ram", "disk"]
    for key in keys:
        match = re.search(rf"<<<{key}>>>\n?(.*?)(?=<<<|$)", output, re.DOTALL)
        result[key] = match.group(1).strip() if match else "N/A"
    return result
