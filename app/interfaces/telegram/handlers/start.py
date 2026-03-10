from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BotCommand, Message


class StartHandler:
    COMMAND_CATALOG: tuple[tuple[str, str], ...] = (
        ("start", "Show welcome message"),
        ("help", "Show full command reference"),
        ("connect", "Connect to SSH server"),
        ("disconnect", "Disconnect active session"),
        ("switch", "Switch active session"),
        ("status", "Show active sessions"),
        ("sessions", "List active sessions"),
        ("history", "Show connection history"),
        ("save", "Save current server"),
        ("quick", "Quick-connect saved server"),
        ("servers", "List saved servers"),
        ("delserver", "Delete saved server"),
        ("shell", "Open interactive shell"),
        ("cancel", "Send Ctrl+C"),
        ("pwd", "Show remote working directory"),
        ("exit", "Exit interactive shell"),
        ("group", "Assign servers to group"),
        ("groups", "List groups"),
        ("delgroup", "Delete group"),
        ("macro", "Save command macro"),
        ("macros", "List macros"),
        ("run", "Run saved macro"),
        ("delmacro", "Delete macro"),
        ("download", "Download remote file"),
        ("upload", "Upload replied file"),
        ("monitor", "Show server monitor"),
    )

    WELCOME_TEXT = (
        "🖥 <b>SSH Connection Bot v2</b>\n\n"
        "Manage SSH servers from Telegram with multi-session support and live output streaming.\n\n"
        "Quick start:\n"
        "1) <code>/connect</code> for manual setup\n"
        "2) <code>/save &lt;name&gt; [&lt;default_cwd&gt;]</code> to save current server\n"
        "3) <code>/connect &lt;saved_name&gt;</code> to reconnect and open persistent shell\n"
        "4) Reply to a file/media message with <code>/upload [path]</code>\n\n"
        "Use <code>/help</code> for full command list."
    )

    HELP_TEXT = (
        "📖 <b>Commands</b>\n\n"
        "<b>Connection</b>\n"
        "/connect\n/connect &lt;saved_name&gt;\n/disconnect [name|all]\n/switch [name]\n/status\n/sessions\n/history\n\n"
        "<b>Saved Servers</b>\n"
        "/save &lt;name&gt; [&lt;default_cwd&gt;]\n/quick &lt;name&gt;\n/servers\n/delserver &lt;name&gt;\n\n"
        "<b>Shell</b>\n"
        "/shell\n/cancel\n/pwd\n/exit\n"
        "In interactive mode, shell state is persistent (cd/env/history).\n"
        "Each message you send is written to shell stdin (with Enter).\n"
        "Output streams live and updates in place.\n"
        "While a command is running, any message you send is forwarded to stdin.\n"
        "To run remote commands starting with slash, use <code>//command</code>.\n\n"
        "<b>Groups</b>\n"
        "/group &lt;name&gt; &lt;servers...&gt;\n/groups\n/delgroup &lt;name&gt;\n\n"
        "<b>Macros</b>\n"
        "/macro &lt;name&gt; &lt;command&gt;\n/macros\n/run &lt;name&gt;\n/delmacro &lt;name&gt;\n\n"
        "<b>Other</b>\n"
        "/download &lt;remote_path&gt;\n"
        "/upload [&lt;remote_path&gt;] (reply to a file/media message)\n"
        "(legacy: caption on file message: /upload [&lt;path&gt;])\n"
        "/monitor"
    )

    def __init__(self) -> None:
        self.router = Router(name="start")
        self.router.message.register(self.cmd_start, Command("start"))
        self.router.message.register(self.cmd_help, Command("help"))

    async def cmd_start(self, message: Message) -> None:
        await message.answer(self.WELCOME_TEXT)

    async def cmd_help(self, message: Message) -> None:
        await message.answer(self.HELP_TEXT)

    @classmethod
    def bot_commands(cls) -> list[BotCommand]:
        return [BotCommand(command=command, description=description) for command, description in cls.COMMAND_CATALOG]
