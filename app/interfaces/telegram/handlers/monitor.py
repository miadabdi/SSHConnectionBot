from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.application.services import MonitorService
from app.domain.errors import SessionUnavailableError
from app.utils.formatting import Formatter


class MonitorHandler:
    def __init__(self, service: MonitorService) -> None:
        self.service = service
        self.router = Router(name="monitor")
        self.router.message.register(self.cmd_monitor, Command("monitor"))

    async def cmd_monitor(self, message: Message) -> None:
        status = await message.answer("📊 Gathering system info...")

        try:
            session, snapshot = await self.service.monitor(message.from_user.id)
        except SessionUnavailableError:
            await status.edit_text("ℹ️ No active SSH session. Use /connect first.")
            return
        except Exception as exc:
            await status.edit_text(f"❌ Monitor failed: {Formatter.escape_html(str(exc))}")
            return

        dashboard = (
            f"📊 <b>System Monitor</b> — <code>{session.name}</code> ({session.host})\n\n"
            f"🖥 <b>OS:</b> {Formatter.escape_html(snapshot.os)}\n"
            f"🏷 <b>Hostname:</b> {Formatter.escape_html(snapshot.hostname)}\n"
            f"⏱ <b>Uptime:</b> {Formatter.escape_html(snapshot.uptime)}\n"
            f"⚡ <b>CPU:</b> {Formatter.escape_html(snapshot.cpu_cores)} cores, load {Formatter.escape_html(snapshot.load)}\n"
            f"🧠 <b>RAM:</b> {snapshot.ram_used} / {snapshot.ram_total} (avail: {snapshot.ram_available})\n"
            f"💾 <b>Disk:</b> {snapshot.disk_used} / {snapshot.disk_total} ({snapshot.disk_percent})"
        )
        await status.edit_text(dashboard)
