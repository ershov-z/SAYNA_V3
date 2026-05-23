from __future__ import annotations

import json
import logging
import re
from typing import Any

from bot.config import Settings
from bot.services.chad_ai import ChadAIClient, ChadAIUnavailableError
from bot.services.memory import MemPalaceService

logger = logging.getLogger(__name__)


class GroupIntentScorer:
    """Estimate whether a group message is addressed to Saina."""

    def __init__(self, settings: Settings, llm: ChadAIClient, memory: MemPalaceService) -> None:
        self.settings = settings
        self.llm = llm
        self.memory = memory

    @staticmethod
    def _parse_score(raw: str) -> int:
        text = raw.strip()
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1]).strip()
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                score = payload.get("score", 0)
                if isinstance(score, int | float):
                    return max(0, min(10, int(round(score))))
        except json.JSONDecodeError:
            pass
        digits = re.findall(r"\d+", text)
        if digits:
            return max(0, min(10, int(digits[0])))
        return 0

    async def score_group_message(
        self,
        *,
        chat_id: int,
        message_text: str,
        bot_username: str | None,
        replied_to_bot: bool,
    ) -> int:
        recent = await self.memory.get_recent_chat_messages(chat_id=chat_id, limit=10)
        history_lines = "\n".join(item.get("content", "") for item in recent) if recent else "history_empty"
        username = bot_username or "unknown"

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "Оцени, обращаются ли к боту-ассистенту Сайна в групповом чате. "
                    "Верни ТОЛЬКО JSON формата {\"score\": число_0_10}. "
                    "0 = точно не к боту, 10 = точно к боту."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Bot username: @{username}\n"
                    f"RepliedToBot: {'yes' if replied_to_bot else 'no'}\n"
                    f"Last10Messages:\n{history_lines}\n\n"
                    f"CurrentMessage:\n{message_text}"
                ),
            },
        ]
        try:
            raw = await self.llm.complete(
                messages,
                max_tokens=2000,
                model=self.settings.chad_intent_model,
                timeout_seconds=self.settings.chad_intent_timeout_seconds,
                retry_on_timeout=1,
                strict=True,
            )
        except ChadAIUnavailableError as exc:
            score = self._fallback_score(message_text=message_text, bot_username=bot_username, replied_to_bot=replied_to_bot)
            logger.warning(
                "intent_score_fallback chat_id=%s reason=llm_unavailable score=%s error=%s message=%r",
                chat_id,
                score,
                exc,
                message_text,
            )
            return score
        score = self._parse_score(raw)
        logger.info(
            "intent_score chat_id=%s model=%s timeout=%.2f recent_count=%s score=%s current_message=%r raw=%r",
            chat_id,
            self.settings.chad_intent_model,
            self.settings.chad_intent_timeout_seconds,
            len(recent),
            score,
            message_text,
            raw,
        )
        return score

    @staticmethod
    def _fallback_score(*, message_text: str, bot_username: str | None, replied_to_bot: bool) -> int:
        lowered = (message_text or "").lower()
        if replied_to_bot:
            return 10
        if bot_username and f"@{bot_username.lower()}" in lowered:
            return 9
        if any(alias in lowered for alias in ("сайна", "saina", "бот", "ассистент")):
            return 8
        if lowered.strip().endswith("?"):
            return 6
        return 1
