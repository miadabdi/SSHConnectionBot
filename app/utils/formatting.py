import html
import re

MAX_MESSAGE_LENGTH = 4096
ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))"
)
RAW_SGR_RE = re.compile(r"\[[0-9;]*m")
RAW_BRACKETED_PASTE_RE = re.compile(r"\[\?2004[hl]")
SSHBOT_MARKER_RE = re.compile(r"^__SSHBOT_[^\n]*$", re.MULTILINE)
OSC_PROMPT_NOISE_RE = re.compile(r"^0;[^\n]*\$\s*", re.MULTILINE)
SHELL_PROMPT_LINE_RE = re.compile(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:[^\n]*[$#]\s*$", re.MULTILINE)


class Formatter:
    @staticmethod
    def escape_html(text: str) -> str:
        return html.escape(text, quote=False)

    @staticmethod
    def format_bash(text: str) -> str:
        escaped = html.escape(Formatter.clean_terminal_output(text), quote=False)
        return f'<pre><code class="language-bash">{escaped}</code></pre>'

    @staticmethod
    def clean_terminal_output(text: str) -> str:
        cleaned = ANSI_ESCAPE_RE.sub("", text)
        cleaned = RAW_BRACKETED_PASTE_RE.sub("", cleaned)
        cleaned = RAW_SGR_RE.sub("", cleaned)
        cleaned = SSHBOT_MARKER_RE.sub("", cleaned)
        cleaned = OSC_PROMPT_NOISE_RE.sub("", cleaned)
        cleaned = SHELL_PROMPT_LINE_RE.sub("", cleaned)
        return cleaned

    @staticmethod
    def truncate(text: str, max_length: int = MAX_MESSAGE_LENGTH) -> str:
        wrapper = '<pre><code class="language-bash"></code></pre>'
        available = max_length - len(wrapper) - 20
        if len(text) <= available:
            return text
        trimmed = text[-available:]
        line_break = trimmed.find("\n")
        if line_break != -1 and line_break < len(trimmed) // 2:
            trimmed = trimmed[line_break + 1 :]
        return "... (truncated)\n" + trimmed
