from __future__ import annotations

import pytest

from bot.config import Settings
from bot.services.intent_scorer import GroupIntentScorer


class FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response

    async def complete(self, messages, max_tokens=100000, *, model=None, timeout_seconds=None):  # noqa: ANN001
        return self.response


class FakeMemory:
    async def get_recent_chat_messages(self, chat_id: int, limit: int = 10):  # noqa: ANN001
        return [
            {"role": "user", "content": "[user_1] Ребят, кто закроет дедлайн?"},
            {"role": "assistant", "content": "[saina] Я слежу за сроками."},
        ][:limit]


def make_settings() -> Settings:
    return Settings(
        TELEGRAM_BOT_TOKEN="token",
        CHAD_AI_API_KEY="key",
        GOOGLE_SHEET_ID="sheet",
    )


@pytest.mark.asyncio
async def test_intent_scorer_reads_json_score() -> None:
    scorer = GroupIntentScorer(make_settings(), FakeLLM('{"score": 8}'), FakeMemory())
    score = await scorer.score_group_message(
        chat_id=-1001,
        message_text="а что по заказам на завтра?",
        bot_username="saina_bot",
        replied_to_bot=False,
    )
    assert score == 8


@pytest.mark.asyncio
async def test_intent_scorer_fallback_digits() -> None:
    scorer = GroupIntentScorer(make_settings(), FakeLLM("score: 11"), FakeMemory())
    score = await scorer.score_group_message(
        chat_id=-1001,
        message_text="подскажи по дедлайну",
        bot_username="saina_bot",
        replied_to_bot=False,
    )
    assert score == 10
