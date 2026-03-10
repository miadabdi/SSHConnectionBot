import logging
import random
from dataclasses import dataclass

from aiogram import Bot

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StreamState:
    message_id: int | None = None
    last_text: str = ""


class HybridStreamPublisher:
    """Streams output by sending a message once, then editing that same message."""

    def __init__(self, bot: Bot, bot_token: str) -> None:
        self._bot = bot
        self._states: dict[tuple[int, int], StreamState] = {}

    def generate_stream_id(self) -> int:
        return random.randint(1, 2**31 - 1)

    async def publish(self, chat_id: int, stream_id: int, text: str, parse_mode: str = "HTML") -> bool:
        key = (chat_id, stream_id)
        state = self._states.setdefault(key, StreamState())
        return await self._publish_classic(chat_id=chat_id, state=state, text=text, parse_mode=parse_mode)

    async def _publish_classic(self, chat_id: int, state: StreamState, text: str, parse_mode: str) -> bool:
        if state.last_text == text:
            return True

        try:
            if state.message_id is None:
                sent = await self._bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
                state.message_id = sent.message_id
                state.last_text = text
                return True

            await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=state.message_id,
                text=text,
                parse_mode=parse_mode,
            )
            state.last_text = text
            return True
        except Exception as exc:
            if "message is not modified" in str(exc).lower():
                state.last_text = text
                return True
            # Try recovering from edit failures by sending a new message.
            logger.debug("Classic stream publish fallback send due to edit error: %s", exc)
            try:
                sent = await self._bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
                state.message_id = sent.message_id
                state.last_text = text
                return True
            except Exception as send_exc:
                logger.error("Classic stream publish failed: %s", send_exc)
                return False
