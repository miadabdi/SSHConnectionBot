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


def test_clean_terminal_output_strips_control_noise() -> None:
    noisy = "\x1b[01;34mfolder\x1b[0m [\x1b[?2004h ]0;user@host: ~ user@host:~$"
    cleaned = Formatter.clean_terminal_output(noisy)
    assert "01;34m" not in cleaned
    assert "[?2004h" not in cleaned
    assert "folder" in cleaned
