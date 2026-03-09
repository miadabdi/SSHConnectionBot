import html

MAX_MESSAGE_LENGTH = 4096


class Formatter:
    @staticmethod
    def escape_html(text: str) -> str:
        return html.escape(text, quote=False)

    @staticmethod
    def format_bash(text: str) -> str:
        escaped = html.escape(text, quote=False)
        return f'<pre><code class="language-bash">{escaped}</code></pre>'

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
