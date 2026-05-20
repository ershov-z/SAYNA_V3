from __future__ import annotations

from bot.services.image_prompt_service import ImagePromptService


def test_image_prompt_service_detects_saina_selfie_request() -> None:
    service = ImagePromptService()
    text = "Сайна, сгенерируй свою фотку для аватарки"
    assert service.is_image_request(text) is True
    assert service.is_saina_request(text) is True


def test_image_prompt_service_includes_saina_appearance_for_self_requests() -> None:
    service = ImagePromptService()
    prompt = service.build_base_prompt("Сделай селфи Сайны в мастерской")
    assert "Персонаж: Сайна" in prompt
    assert "Сюжет/запрос пользователя" in prompt


def test_image_prompt_service_keeps_non_saina_prompt_as_is() -> None:
    service = ImagePromptService()
    prompt = service.build_base_prompt("Нарисуй красный плащ на манекене")
    assert prompt == "Нарисуй красный плащ на манекене"
