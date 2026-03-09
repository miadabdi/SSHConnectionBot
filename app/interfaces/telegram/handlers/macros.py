from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.application.services import MacroService
from app.domain.errors import NotFoundError
from app.utils.validators import is_valid_slug


class MacroHandler:
    def __init__(self, service: MacroService) -> None:
        self.service = service
        self._execute_callback = None
        self.router = Router(name="macros")
        self._register()

    def _register(self) -> None:
        self.router.message.register(self.cmd_macro, Command("macro"))
        self.router.message.register(self.cmd_macros, Command("macros"))
        self.router.message.register(self.cmd_run, Command("run"))
        self.router.message.register(self.cmd_delmacro, Command("delmacro"))

    def set_execute_callback(self, callback) -> None:
        self._execute_callback = callback

    async def cmd_macro(self, message: Message) -> None:
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 3:
            await message.answer("❌ Usage: <code>/macro &lt;name&gt; &lt;command&gt;</code>")
            return

        name = parts[1].strip().lower()
        command = parts[2].strip()

        if not is_valid_slug(name):
            await message.answer("❌ Invalid macro name.")
            return

        await self.service.save_macro(user_id=message.from_user.id, name=name, command=command)
        await message.answer(f"💾 Macro <code>{name}</code> saved.")

    async def cmd_macros(self, message: Message) -> None:
        macros = await self.service.list_macros(message.from_user.id)
        if not macros:
            await message.answer("ℹ️ No saved macros.")
            return

        lines = ["📝 <b>Saved Macros</b>\n"]
        for macro in macros:
            lines.append(f"▸ <code>{macro.name}</code>: <code>{macro.command}</code>")
        await message.answer("\n".join(lines))

    async def cmd_run(self, message: Message) -> None:
        args = (message.text or "").split(maxsplit=1)
        if len(args) < 2:
            await message.answer("❌ Usage: <code>/run &lt;name&gt;</code>")
            return

        name = args[1].strip().lower()
        try:
            macro = await self.service.get_macro(user_id=message.from_user.id, name=name)
        except NotFoundError:
            await message.answer(f"❌ Macro <code>{name}</code> not found.")
            return

        await message.answer(f"🔄 Running macro <code>{name}</code>: <code>{macro.command}</code>")
        if self._execute_callback:
            await self._execute_callback(message, macro.command)

    async def cmd_delmacro(self, message: Message) -> None:
        args = (message.text or "").split(maxsplit=1)
        if len(args) < 2:
            await message.answer("❌ Usage: <code>/delmacro &lt;name&gt;</code>")
            return

        name = args[1].strip().lower()
        deleted = await self.service.delete_macro(user_id=message.from_user.id, name=name)
        if deleted:
            await message.answer(f"🗑 Macro <code>{name}</code> deleted.")
        else:
            await message.answer(f"❌ Macro <code>{name}</code> not found.")
