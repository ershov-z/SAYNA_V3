from __future__ import annotations

import pytest

from bot.config import Settings
from bot.models.message_flow import MessageEnvelope, ModuleName
from bot.services.decision_maker import DecisionMakerService
from bot.services.soul import SoulService


class FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response

    async def complete(self, messages, max_tokens=100000, *, model=None, timeout_seconds=None, images=None):  # noqa: ANN001
        return self.response


def make_settings(**overrides) -> Settings:
    payload = {
        "TELEGRAM_BOT_TOKEN": "token",
        "CHAD_AI_API_KEY": "key",
        "GOOGLE_SHEET_ID": "sheet",
        "CHAD_DECISION_MIN_CONFIDENCE": 0.35,
    }
    payload.update(overrides)
    return Settings(**payload)


@pytest.mark.asyncio
async def test_decision_maker_parses_valid_module() -> None:
    settings = make_settings()
    service = DecisionMakerService(settings, FakeLLM('{"module":"secretary","confidence":0.88,"reason":"crud"}'), SoulService(settings))
    decision = await service.decide(
        MessageEnvelope(user_id=1, chat_id=2, text="покажи заказы", images=[]),
    )
    assert decision.module == ModuleName.SECRETARY
    assert decision.fallback_used is False
    assert decision.confidence == pytest.approx(0.88)


@pytest.mark.asyncio
async def test_decision_maker_fallback_on_invalid_payload() -> None:
    settings = make_settings()
    service = DecisionMakerService(settings, FakeLLM("not json"), SoulService(settings))
    decision = await service.decide(
        MessageEnvelope(user_id=1, chat_id=2, text="просто поболтаем", images=[]),
    )
    assert decision.module == ModuleName.CHAT
    assert decision.fallback_used is True


@pytest.mark.asyncio
async def test_decision_maker_fallback_on_low_confidence() -> None:
    settings = make_settings(CHAD_DECISION_MIN_CONFIDENCE=0.7)
    service = DecisionMakerService(settings, FakeLLM('{"module":"generation","confidence":0.45,"reason":"maybe"}'), SoulService(settings))
    decision = await service.decide(
        MessageEnvelope(user_id=1, chat_id=2, text="сделай картинку", images=[]),
    )
    assert decision.module == ModuleName.CHAT
    assert decision.fallback_used is True


@pytest.mark.asyncio
async def test_decision_maker_fallback_routes_reminder_to_secretary() -> None:
    settings = make_settings(CHAD_DECISION_MIN_CONFIDENCE=0.7)
    service = DecisionMakerService(settings, FakeLLM("not json"), SoulService(settings))
    decision = await service.decide(
        MessageEnvelope(user_id=1, chat_id=2, text="через минуту напомни Кате закрыть дверь", images=[]),
    )
    assert decision.module == ModuleName.SECRETARY
    assert decision.fallback_used is True
