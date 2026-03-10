from app.interfaces.telegram.handlers.start import StartHandler


def test_bot_commands_include_core_entries() -> None:
    commands = StartHandler.bot_commands()
    command_names = {item.command for item in commands}

    assert "connect" in command_names
    assert "save" in command_names
    assert "shell" in command_names
    assert "enter" in command_names
    assert "upload" in command_names
    assert "monitor" in command_names
    assert "help" in command_names
