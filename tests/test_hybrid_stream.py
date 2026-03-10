from types import SimpleNamespace

import pytest

from app.infrastructure.telegram.hybrid_stream import HybridStreamPublisher


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, str]] = []
        self.edited: list[tuple[int, int, str, str]] = []
        self._next_message_id = 100

    async def send_message(self, chat_id: int, text: str, parse_mode: str = "HTML"):
        self.sent.append((chat_id, text, parse_mode))
        self._next_message_id += 1
        return SimpleNamespace(message_id=self._next_message_id)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, parse_mode: str = "HTML"):
        self.edited.append((chat_id, message_id, text, parse_mode))
        return True


@pytest.mark.asyncio
async def test_publish_uses_classic_send_then_edit() -> None:
    bot = _FakeBot()
    publisher = HybridStreamPublisher(bot=bot, bot_token="unused-token")

    stream_id = 42
    ok1 = await publisher.publish(chat_id=1, stream_id=stream_id, text="line 1", parse_mode="HTML")
    ok2 = await publisher.publish(chat_id=1, stream_id=stream_id, text="line 1\nline 2", parse_mode="HTML")

    assert ok1 is True
    assert ok2 is True
    assert len(bot.sent) == 1
    assert len(bot.edited) == 1
    assert bot.sent[0][1] == "line 1"
    assert bot.edited[0][2] == "line 1\nline 2"


@pytest.mark.asyncio
async def test_publish_skips_duplicate_text_updates() -> None:
    bot = _FakeBot()
    publisher = HybridStreamPublisher(bot=bot, bot_token="unused-token")

    stream_id = 77
    ok1 = await publisher.publish(chat_id=1, stream_id=stream_id, text="same", parse_mode="HTML")
    ok2 = await publisher.publish(chat_id=1, stream_id=stream_id, text="same", parse_mode="HTML")

    assert ok1 is True
    assert ok2 is True
    assert len(bot.sent) == 1
    assert len(bot.edited) == 0
