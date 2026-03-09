from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message


class StartHandler:
    WELCOME_TEXT = (
        "🖥 <b>SSH Connection Bot v2</b>\n\n"
        "Manage SSH servers from Telegram with multi-session support and live output streaming.\n\n"
        "Use /connect to start a new connection."
    )

    HELP_TEXT = (
        "📖 <b>Commands</b>\n\n"
        "<b>Connection</b>\n"
        "/connect\n/disconnect [name|all]\n/switch [name]\n/status\n/history\n\n"
        "<b>Saved Servers</b>\n"
        "/save &lt;name&gt;\n/quick &lt;name&gt;\n/servers\n/delserver &lt;name&gt;\n\n"
        "<b>Groups</b>\n"
        "/group &lt;name&gt; &lt;servers...&gt;\n/groups\n/delgroup &lt;name&gt;\n\n"
        "<b>Macros</b>\n"
        "/macro &lt;name&gt; &lt;command&gt;\n/macros\n/run &lt;name&gt;\n/delmacro &lt;name&gt;\n\n"
        "<b>Other</b>\n"
        "/download &lt;remote_path&gt;\n(upload file with caption /upload &lt;path&gt;)\n/monitor\n/shell\n/exit"
    )

    def __init__(self) -> None:
        self.router = Router(name="start")
        self.router.message.register(self.cmd_start, Command("start"))
        self.router.message.register(self.cmd_help, Command("help"))

    async def cmd_start(self, message: Message) -> None:
        await message.answer(self.WELCOME_TEXT)

    async def cmd_help(self, message: Message) -> None:
        await message.answer(self.HELP_TEXT)
