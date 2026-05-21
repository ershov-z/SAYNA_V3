from __future__ import annotations

import pytest

from bot.config import Settings
from bot.services.image_generation import ChadImageService


class FakePromptService:
    def __init__(self, base_prompt: str, is_saina: bool = True) -> None:
        self.base_prompt = base_prompt
        self.is_saina = is_saina

    def is_image_request(self, text: str) -> bool:  # noqa: ARG002
        return True

    def build_base_prompt(self, user_text: str) -> str:  # noqa: ARG002
        return self.base_prompt

    def is_saina_request(self, user_text: str) -> bool:  # noqa: ARG002
        return self.is_saina

    def build_caption(self, user_text: str) -> str:  # noqa: ARG002
        return "caption"


class FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_messages = None

    async def complete(self, messages, max_tokens=100000, *, model=None, timeout_seconds=None, images=None):  # noqa: ANN001, ARG002
        self.last_messages = messages
        return self.response


def make_settings(**overrides) -> Settings:
    payload = {
        "TELEGRAM_BOT_TOKEN": "token",
        "CHAD_AI_API_KEY": "key",
        "GOOGLE_SHEET_ID": "sheet",
    }
    payload.update(overrides)
    return Settings(**payload)


@pytest.mark.asyncio
async def test_image_prompt_builder_falls_back_when_llm_returns_error_stub() -> None:
    settings = make_settings()
    prompt_service = FakePromptService(base_prompt="base prompt for saina")
    image_service = ChadImageService(settings, prompt_service=prompt_service, llm=FakeLLM("Не смог сформировать ответ, попробуй переформулировать вопрос."))
    try:
        prompt = await image_service._build_final_prompt("Сайна, нарисуй себя")  # noqa: SLF001
    finally:
        await image_service.close()
    assert prompt == "base prompt for saina"


@pytest.mark.asyncio
async def test_image_prompt_builder_cleans_prompt_prefix() -> None:
    settings = make_settings()
    prompt_service = FakePromptService(base_prompt="base prompt for saina")
    image_service = ChadImageService(
        settings,
        prompt_service=prompt_service,
        llm=FakeLLM("Prompt: cinematic portrait of Saina in modern workshop, soft light, anime style"),
    )
    try:
        prompt = await image_service._build_final_prompt("Сайна, нарисуй себя")  # noqa: SLF001
    finally:
        await image_service.close()
    assert prompt == "cinematic portrait of Saina in modern workshop, soft light, anime style"


@pytest.mark.asyncio
async def test_image_prompt_builder_requires_russian_output_for_saina() -> None:
    settings = make_settings()
    prompt_service = FakePromptService(base_prompt="base prompt for saina")
    llm = FakeLLM("готовый промпт")
    image_service = ChadImageService(settings, prompt_service=prompt_service, llm=llm)
    try:
        await image_service._build_final_prompt("Сайна, нарисуй себя")  # noqa: SLF001
    finally:
        await image_service.close()

    assert llm.last_messages is not None
    system_message = llm.last_messages[0]["content"]
    assert "только на русском языке" in system_message
    assert "переведи их на русский" in system_message
