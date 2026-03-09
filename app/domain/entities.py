from dataclasses import dataclass
from datetime import datetime
from typing import Literal

AuthType = Literal["password", "key"]


@dataclass(slots=True)
class SSHSessionSnapshot:
    user_id: int
    name: str
    host: str
    port: int
    username: str
    auth_type: AuthType
    is_interactive: bool


@dataclass(slots=True)
class SessionHistoryEntry:
    user_id: int
    host: str
    port: int
    username: str
    session_name: str
    auth_type: AuthType
    is_active: bool
    created_at: datetime
    encrypted_password: str = ""
    encrypted_key: str = ""


@dataclass(slots=True)
class SavedServer:
    user_id: int
    name: str
    host: str
    port: int
    username: str
    auth_type: AuthType
    group: str
    encrypted_password: str
    encrypted_key: str


@dataclass(slots=True)
class Macro:
    user_id: int
    name: str
    command: str


@dataclass(slots=True)
class ServerGroup:
    user_id: int
    name: str


@dataclass(slots=True)
class CommandResult:
    exit_code: int
    output: str


@dataclass(slots=True)
class MonitorSnapshot:
    os: str
    hostname: str
    uptime: str
    cpu_cores: str
    load: str
    ram_total: str
    ram_used: str
    ram_available: str
    disk_total: str
    disk_used: str
    disk_percent: str
