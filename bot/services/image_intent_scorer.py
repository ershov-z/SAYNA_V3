from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from bot.config import Settings
from bot.services.chad_ai import ChadAIClient

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ImageIntentDecision:
    score: int
    should_generate: bool


class ImageIntentScorer:
    STRONG_PATTERNS = (
        r"\bсгенер\w*",
        r"\bнарис\w*",
        r"\bсоздай\s+(?:изображен\w*|картин\w*|арт)\b",
        r"\bсделай\s+(?:картин\w*|арт|иллюстрац\w*|фото)\b",
        r"\bпришли\s+(?:картин\w*|изображен\w*|арт|фото)\b",
        r"\bопиши\s+себя\b",
        r"\bпокажи\s+себя\b",
        r"\bкак\s+ты\s+выглядишь\b",
        r"\bселфи\b",
    )
    SOFT_PATTERNS = (
        r"\bкартинк\w*\b",
        r"\bизображен\w*\b",
        r"\bарт\b",
        r"\bфото\b",
        r"\bгенерац\w*\b",
    )

    def __init__(self, settings: Settings, llm: ChadAIClient) -> None:
        self.settings = settings
        self.llm = llm

    @staticmethod
    def _parse_score(raw: str) -> int:
        text = raw.strip()
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                value = parsed.get("score")
                if isinstance(value, int | float):
                    return max(0, min(10, int(value)))
        except json.JSONDecodeError:
            pass
        nums = re.findall(r"\d+", text)
        if nums:
            return max(0, min(10, int(nums[0])))
        return 0

    @staticmethod
    def _has_any(text: str, patterns: tuple[str, ...]) -> bool:
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

    async def score(self, text: str) -> ImageIntentDecision:
        lowered = text.lower().strip()
        if not lowered:
            return ImageIntentDecision(score=0, should_generate=False)

        if self._has_any(lowered, self.STRONG_PATTERNS):
            score = 10
            return ImageIntentDecision(score=score, should_generate=True)

        if not self._has_any(lowered, self.SOFT_PATTERNS):
            return ImageIntentDecision(score=0, should_generate=False)

        messages = [
            {
                "role": "system",
                "content": (
                    "Оцени, просит ли пользователь СЕЙЧАС сгенерировать новое изображение. "
                    "Верни строго JSON: {\"score\": 0..10}. "
                    "10 = прямой явный запрос сгенерировать/прислать картинку, "
                    "0 = просто обсуждение/упоминание слова картинка без запроса генерации."
                ),
            },
            {"role": "user", "content": lowered},
        ]
        raw = await self.llm.complete(
            messages,
            max_tokens=100000,
            model=self.settings.chad_image_intent_model,
            timeout_seconds=self.settings.chad_image_intent_timeout_seconds,
        )
        score = self._parse_score(raw)
        should = score >= self.settings.chad_image_intent_threshold
        logger.info(
            "image_intent_eval score=%s threshold=%s should_generate=%s text=%r raw=%r",
            score,
            self.settings.chad_image_intent_threshold,
            should,
            lowered[:220],
            raw[:220],
        )
        return ImageIntentDecision(score=score, should_generate=should)
