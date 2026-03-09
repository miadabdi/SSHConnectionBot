from app.application.services import parse_monitor_output


def test_parse_monitor_output() -> None:
    raw = """<<<os>>>\nLinux\n<<<hostname>>>\nbox\n<<<ram>>>\n8G 1G 7G\n"""
    parsed = parse_monitor_output(raw)
    assert parsed["os"] == "Linux"
    assert parsed["hostname"] == "box"
    assert parsed["ram"] == "8G 1G 7G"
