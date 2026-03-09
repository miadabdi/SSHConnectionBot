from app.application.services import parse_monitor_output
from app.domain.entities import MonitorSnapshot

MONITOR_COMMANDS = {
    "os": "uname -sr 2>/dev/null || echo 'Unknown'",
    "hostname": "hostname 2>/dev/null || echo 'Unknown'",
    "uptime": "uptime -p 2>/dev/null || uptime | sed 's/.*up/up/' 2>/dev/null || echo 'Unknown'",
    "cpu_cores": "nproc 2>/dev/null || echo '?'",
    "load": "cat /proc/loadavg 2>/dev/null | awk '{print $1, $2, $3}' || echo '?'",
    "ram": "free -h 2>/dev/null | awk '/^Mem:/{print $2, $3, $7}' || echo '?'",
    "disk": "df -h / 2>/dev/null | awk 'NR==2{print $2, $3, $5}' || echo '?'",
}


class SSHMonitorCollector:
    async def collect(self, session) -> MonitorSnapshot:
        combined_command = " && ".join([f"echo '<<<{key}>>>' && {cmd}" for key, cmd in MONITOR_COMMANDS.items()])
        output_chunks: list[str] = []

        async def collect_chunk(chunk: str) -> None:
            output_chunks.append(chunk)

        await session.execute(combined_command, collect_chunk)
        values = parse_monitor_output("".join(output_chunks))

        ram_parts = values.get("ram", "? ? ?").split()
        disk_parts = values.get("disk", "? ? ?").split()

        return MonitorSnapshot(
            os=values.get("os", "N/A"),
            hostname=values.get("hostname", "N/A"),
            uptime=values.get("uptime", "N/A"),
            cpu_cores=values.get("cpu_cores", "?"),
            load=values.get("load", "?"),
            ram_total=ram_parts[0] if len(ram_parts) > 0 else "?",
            ram_used=ram_parts[1] if len(ram_parts) > 1 else "?",
            ram_available=ram_parts[2] if len(ram_parts) > 2 else "?",
            disk_total=disk_parts[0] if len(disk_parts) > 0 else "?",
            disk_used=disk_parts[1] if len(disk_parts) > 1 else "?",
            disk_percent=disk_parts[2] if len(disk_parts) > 2 else "?",
        )
