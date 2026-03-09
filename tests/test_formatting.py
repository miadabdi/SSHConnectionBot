from app.utils.formatting import Formatter


def test_format_bash_wraps_output() -> None:
    rendered = Formatter.format_bash("echo hi")
    assert rendered.startswith("<pre><code")
    assert "echo hi" in rendered


def test_truncate_keeps_tail() -> None:
    text = "x" * 7000
    truncated = Formatter.truncate(text)
    assert truncated.startswith("... (truncated)")
    assert len(truncated) < len(text)
