from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.application.services import GroupService
from app.utils.validators import is_valid_slug


class GroupHandler:
    def __init__(self, service: GroupService) -> None:
        self.service = service
        self.router = Router(name="groups")
        self._register()

    def _register(self) -> None:
        self.router.message.register(self.cmd_group, Command("group"))
        self.router.message.register(self.cmd_groups, Command("groups"))
        self.router.message.register(self.cmd_delgroup, Command("delgroup"))

    async def cmd_group(self, message: Message) -> None:
        parts = (message.text or "").split()
        if len(parts) < 3:
            await message.answer(
                "❌ Usage: <code>/group &lt;group_name&gt; &lt;server1&gt; [server2] ...</code>"
            )
            return

        group_name = parts[1].strip().lower()
        server_names = [name.strip().lower() for name in parts[2:]]

        if not is_valid_slug(group_name):
            await message.answer("❌ Invalid group name.")
            return

        assigned, missing = await self.service.upsert_and_assign(
            user_id=message.from_user.id,
            group_name=group_name,
            server_names=server_names,
        )

        lines = [f"📁 Group <code>{group_name}</code> updated"]
        if assigned:
            lines.append(f"✅ Assigned: {', '.join(f'<code>{item}</code>' for item in assigned)}")
        if missing:
            lines.append(f"⚠️ Not found: {', '.join(f'<code>{item}</code>' for item in missing)}")

        await message.answer("\n".join(lines))

    async def cmd_groups(self, message: Message) -> None:
        grouped = await self.service.list_groups_with_servers(message.from_user.id)
        if not grouped:
            await message.answer("ℹ️ No server groups.")
            return

        lines = ["📁 <b>Server Groups</b>\n"]
        for group_name, servers in grouped.items():
            rendered = ", ".join(f"<code>{server.name}</code>" for server in servers) if servers else "<i>empty</i>"
            lines.append(f"📂 <b>{group_name}</b>: {rendered}")

        await message.answer("\n".join(lines))

    async def cmd_delgroup(self, message: Message) -> None:
        args = (message.text or "").split(maxsplit=1)
        if len(args) < 2:
            await message.answer("❌ Usage: <code>/delgroup &lt;name&gt;</code>")
            return

        group_name = args[1].strip().lower()
        deleted = await self.service.delete_group(user_id=message.from_user.id, group_name=group_name)
        if deleted:
            await message.answer(f"🗑 Group <code>{group_name}</code> deleted. Servers unassigned.")
        else:
            await message.answer(f"❌ Group <code>{group_name}</code> not found.")
