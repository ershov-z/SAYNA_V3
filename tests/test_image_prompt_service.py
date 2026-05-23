from __future__ import annotations

from bot.services.image_prompt_service import ImagePromptService


def test_image_prompt_service_classifies_self_generation() -> None:
    service = ImagePromptService()
    text = "Сайна, сгенерируй свою фотку для аватарки"
    assert service.classify_generation_mode(text) is ImagePromptService.GenerationMode.SELF


def test_image_prompt_service_builds_short_self_prompt() -> None:
    service = ImagePromptService()
    prompt = service.build_self_prompt("Сайна, скинь свое селфи в клоунском гриме")
    assert prompt == "Селфи персонажа в клоунском гриме"


def test_image_prompt_service_extracts_simple_prompt() -> None:
    service = ImagePromptService()
    prompt = service.build_simple_prompt("Сайна, сгенерируй комикс про умамусуме")
    assert prompt == "комикс про умамусуме"


def test_image_prompt_service_classifies_simple_generation() -> None:
    service = ImagePromptService()
    text = "Сгенерируй комикс про боевых роботов"
    assert service.classify_generation_mode(text) is ImagePromptService.GenerationMode.SIMPLE


def test_image_prompt_service_classifies_photo_of_self_as_self_generation() -> None:
    service = ImagePromptService()
    text = "Сайна, скинь фотку себя в виде гонщика формулы 1"
    assert service.classify_generation_mode(text) is ImagePromptService.GenerationMode.SELF
