import logging
import random
from dataclasses import dataclass

import aiohttp
from aiogram import Bot

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StreamState:
    mode: str = "draft"  # draft | classic
    message_id: int | None = None


class HybridStreamPublisher:
    """Attempts Bot API draft streaming first and falls back to standard message editing."""

    def __init__(self, bot: Bot, bot_token: str) -> None:
        self._bot = bot
        self._base_url = f"https://api.telegram.org/bot{bot_token}"
        self._states: dict[tuple[int, int], StreamState] = {}

    def generate_stream_id(self) -> int:
        return random.randint(1, 2**31 - 1)

    async def publish(self, chat_id: int, stream_id: int, text: str, parse_mode: str = "HTML") -> bool:
        key = (chat_id, stream_id)
        state = self._states.setdefault(key, StreamState())

        if state.mode == "draft":
            ok = await self._publish_draft(chat_id=chat_id, stream_id=stream_id, text=text, parse_mode=parse_mode)
            if ok:
                return True
            state.mode = "classic"

        return await self._publish_classic(chat_id=chat_id, state=state, text=text, parse_mode=parse_mode)

    async def _publish_draft(self, chat_id: int, stream_id: int, text: str, parse_mode: str) -> bool:
        url = f"{self._base_url}/sendMessageDraft"
        payload = {
            "chat_id": chat_id,
            "draft_id": stream_id,
            "text": text,
            "parse_mode": parse_mode,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as response:
                    result = await response.json()
                    if not result.get("ok"):
                        logger.warning("sendMessageDraft failed: %s", result.get("description", "unknown"))
                        return False
                    return True
        except Exception as exc:
            logger.warning("sendMessageDraft request failed: %s", exc)
            return False

    async def _publish_classic(self, chat_id: int, state: StreamState, text: str, parse_mode: str) -> bool:
        try:
            if state.message_id is None:
                sent = await self._bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
                state.message_id = sent.message_id
                return True

            await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=state.message_id,
                text=text,
                parse_mode=parse_mode,
            )
            return True
        except Exception as exc:
            # Try recovering from edit failures by sending a new message.
            logger.debug("Classic stream publish fallback send due to edit error: %s", exc)
            try:
                sent = await self._bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
                state.message_id = sent.message_id
                return True
            except Exception as send_exc:
                logger.error("Classic stream publish failed: %s", send_exc)
                return False
