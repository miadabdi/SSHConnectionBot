from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.application.services import ConnectionService, SavedServerService
from app.domain.errors import NotFoundError, SessionUnavailableError, ValidationError
from app.utils.validators import is_valid_slug


class SavedServerHandler:
    def __init__(self, service: SavedServerService, connection_service: ConnectionService) -> None:
        self.service = service
        self.connection_service = connection_service
        self.router = Router(name="servers")
        self._register()

    def _register(self) -> None:
        self.router.message.register(self.cmd_save, Command("save"))
        self.router.message.register(self.cmd_quick, Command("quick"))
        self.router.message.register(self.cmd_servers, Command("servers"))
        self.router.message.register(self.cmd_delserver, Command("delserver"))

    async def cmd_save(self, message: Message) -> None:
        args = (message.text or "").split(maxsplit=2)
        if len(args) < 2:
            await message.answer("❌ Usage: <code>/save &lt;name&gt; [default_cwd]</code>")
            return

        name = args[1].strip().lower()
        if not is_valid_slug(name):
            await message.answer("❌ Invalid server name.")
            return

        default_cwd = args[2].strip() if len(args) > 2 else None
        if default_cwd == "-":
            default_cwd = ""

        try:
            server = await self.service.save_current_as(
                user_id=message.from_user.id,
                name=name,
                default_cwd=default_cwd,
            )
        except SessionUnavailableError:
            await message.answer("ℹ️ No active SSH session to save.")
            return

        lines = [
            f"💾 Server saved as <code>{server.name}</code>\n"
            f"🖥 {server.host}:{server.port}",
        ]
        if server.default_cwd:
            lines.append(f"📁 Default dir: <code>{server.default_cwd}</code>")
        lines.append(f"Use <code>/connect {server.name}</code> to reconnect and open a shell.")
        await message.answer("\n".join(lines))

    async def cmd_quick(self, message: Message) -> None:
        args = (message.text or "").split(maxsplit=1)
        if len(args) < 2:
            await message.answer("❌ Usage: <code>/quick &lt;name&gt;</code>")
            return

        name = args[1].strip().lower()
        status = await message.answer(f"⏳ Quick connecting to <code>{name}</code>...")

        try:
            session = await self.connection_service.quick_connect(user_id=message.from_user.id, name=name)
        except ValidationError as exc:
            await status.edit_text(f"❌ {exc}")
            return
        except NotFoundError:
            await status.edit_text(f"❌ Saved server <code>{name}</code> not found.")
            return
        except Exception as exc:
            await status.edit_text(f"❌ Quick connect failed: {exc}")
            return

        await status.edit_text(
            f"✅ <b>Connected</b> (session: <code>{session.name}</code>)\n\n"
            f"🖥 Host: <code>{session.host}:{session.port}</code>\n"
            f"👤 User: <code>{session.username}</code>\n"
            f"Tip: use <code>/shell</code> for persistent shell mode."
        )

    async def cmd_servers(self, message: Message) -> None:
        servers = await self.service.list_servers(user_id=message.from_user.id)
        if not servers:
            await message.answer("ℹ️ No saved servers.")
            return

        grouped: dict[str, list] = {}
        for server in servers:
            key = server.group or "Ungrouped"
            grouped.setdefault(key, []).append(server)

        lines = ["💾 <b>Saved Servers</b>\n"]
        for group, items in sorted(grouped.items()):
            if group != "Ungrouped":
                lines.append(f"\n📁 <b>{group}</b>")
            for item in items:
                auth_icon = "🔑" if item.auth_type == "key" else "🔐"
                cwd_suffix = f" | cwd: {item.default_cwd}" if item.default_cwd else ""
                lines.append(
                    f"  {auth_icon} <code>{item.name}</code> — "
                    f"{item.host}:{item.port} as {item.username}{cwd_suffix}"
                )

        await message.answer("\n".join(lines))

    async def cmd_delserver(self, message: Message) -> None:
        args = (message.text or "").split(maxsplit=1)
        if len(args) < 2:
            await message.answer("❌ Usage: <code>/delserver &lt;name&gt;</code>")
            return

        name = args[1].strip().lower()
        deleted = await self.service.delete_server(user_id=message.from_user.id, name=name)
        if deleted:
            await message.answer(f"🗑 Server <code>{name}</code> deleted.")
        else:
            await message.answer(f"❌ Server <code>{name}</code> not found.")
