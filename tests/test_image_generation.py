from __future__ import annotations

import pytest

from bot.config import Settings
from bot.services.image_generation import ChadImageService
from bot.services.image_prompt_service import ImagePromptService


class FakePromptService:
    def __init__(self, mode: ImagePromptService.GenerationMode, *, simple_prompt: str = "", self_prompt: str = "") -> None:
        self.mode = mode
        self.simple_prompt = simple_prompt
        self.self_prompt = self_prompt

    def is_image_request(self, text: str) -> bool:  # noqa: ARG002
        return self.mode is not ImagePromptService.GenerationMode.NONE

    def classify_generation_mode(self, text: str) -> ImagePromptService.GenerationMode:  # noqa: ARG002
        return self.mode

    def build_simple_prompt(self, user_text: str) -> str:  # noqa: ARG002
        return self.simple_prompt

    def build_self_prompt(self, user_text: str) -> str:  # noqa: ARG002
        return self.self_prompt

    def build_caption(self, user_text: str) -> str:  # noqa: ARG002
        return "caption"


class FakeLLM:
    def __init__(self, response: str) -> None:
        self.response = response

    async def complete(self, messages, max_tokens=100000, *, model=None, timeout_seconds=None, images=None):  # noqa: ANN001, ARG002
        return self.response


class FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class FakeHTTPClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.last_json = None
        self.last_endpoint = ""

    async def post(self, endpoint: str, json: dict):  # noqa: ANN001
        self.last_endpoint = endpoint
        self.last_json = json
        return FakeHTTPResponse(self.payload)

    async def aclose(self) -> None:
        return None


def make_settings(**overrides) -> Settings:
    payload = {
        "TELEGRAM_BOT_TOKEN": "token",
        "CHAD_AI_API_KEY": "key",
        "GOOGLE_SHEET_ID": "sheet",
    }
    payload.update(overrides)
    return Settings(**payload)


@pytest.mark.asyncio
async def test_build_generation_inputs_for_simple_passes_user_images() -> None:
    settings = make_settings()
    prompt_service = FakePromptService(
        ImagePromptService.GenerationMode.SIMPLE,
        simple_prompt="комикс про роботов",
    )
    image_service = ChadImageService(settings, prompt_service=prompt_service, llm=FakeLLM("ok"))
    try:
        prompt, attachments, reference_urls, build_error, handled = await image_service._build_generation_inputs(  # noqa: SLF001
            "Сгенерируй комикс",
            user_images=["data:image/jpeg;base64,user1"],
        )
    finally:
        await image_service.close()
    assert handled is True
    assert build_error == ""
    assert prompt == "комикс про роботов"
    assert attachments == ["data:image/jpeg;base64,user1"]
    assert reference_urls == []


@pytest.mark.asyncio
async def test_build_generation_inputs_for_self_adds_reference_first() -> None:
    settings = make_settings(
        CHAD_IMAGE_SELF_REFERENCE_ENABLED=True,
        CHAD_IMAGE_SELF_REFERENCE_REQUIRED=True,
        CHAD_IMAGE_SELF_REFERENCE_URL="https://thumbsnap.com/i/cP46JG2a.jpg?0523",
    )
    prompt_service = FakePromptService(ImagePromptService.GenerationMode.SELF, self_prompt="Селфи персонажа в клоунском гриме")
    image_service = ChadImageService(settings, prompt_service=prompt_service, llm=FakeLLM("ok"))
    try:
        prompt, attachments, reference_urls, build_error, handled = await image_service._build_generation_inputs(  # noqa: SLF001
            "Сайна, скинь селфи",
            user_images=["data:image/jpeg;base64,user1"],
        )
    finally:
        await image_service.close()
    assert handled is True
    assert build_error == ""
    assert prompt == "Селфи персонажа в клоунском гриме"
    assert attachments == ["data:image/jpeg;base64,user1"]
    assert reference_urls == ["https://thumbsnap.com/i/cP46JG2a.jpg?0523"]


@pytest.mark.asyncio
async def test_build_generation_inputs_for_self_fails_when_reference_required_but_missing() -> None:
    settings = make_settings(
        CHAD_IMAGE_SELF_REFERENCE_ENABLED=True,
        CHAD_IMAGE_SELF_REFERENCE_REQUIRED=True,
        CHAD_IMAGE_SELF_REFERENCE_URL="",
    )
    prompt_service = FakePromptService(ImagePromptService.GenerationMode.SELF, self_prompt="Селфи персонажа")
    image_service = ChadImageService(settings, prompt_service=prompt_service, llm=FakeLLM("ok"))
    try:
        prompt, attachments, reference_urls, build_error, handled = await image_service._build_generation_inputs(  # noqa: SLF001
            "Сайна, селфи",
            user_images=[],
        )
    finally:
        await image_service.close()
    assert handled is True
    assert prompt == ""
    assert attachments == []
    assert reference_urls == []
    assert "reference-ссылку Сайны" in build_error


@pytest.mark.asyncio
async def test_imagine_includes_extra_images_field_in_payload() -> None:
    settings = make_settings(CHAD_IMAGE_EXTRA_IMAGES_FIELD="image_base64s")
    prompt_service = FakePromptService(ImagePromptService.GenerationMode.SIMPLE, simple_prompt="кот")
    image_service = ChadImageService(settings, prompt_service=prompt_service, llm=FakeLLM("ok"))
    fake_client = FakeHTTPClient({"status": "failed", "error_message": "upstream-error"})
    image_service._client = fake_client  # noqa: SLF001
    content_id, error = await image_service._imagine("prompt", ["data:image/png;base64,a"])  # noqa: SLF001
    assert content_id == ""
    assert error == "upstream-error"
    assert fake_client.last_endpoint.endswith("/imagine")
    assert fake_client.last_json is not None
    assert fake_client.last_json["image_base64s"] == ["data:image/png;base64,a"]


@pytest.mark.asyncio
async def test_imagine_includes_reference_urls_in_payload() -> None:
    settings = make_settings()
    prompt_service = FakePromptService(ImagePromptService.GenerationMode.SIMPLE, simple_prompt="кот")
    image_service = ChadImageService(settings, prompt_service=prompt_service, llm=FakeLLM("ok"))
    fake_client = FakeHTTPClient({"status": "failed", "error_message": "upstream-error"})
    image_service._client = fake_client  # noqa: SLF001
    content_id, error = await image_service._imagine("prompt", [], ["https://thumbsnap.com/i/cP46JG2a.jpg?0523"])  # noqa: SLF001
    assert content_id == ""
    assert error == "upstream-error"
    assert fake_client.last_json is not None
    assert fake_client.last_json["image_urls"] == ["https://thumbsnap.com/i/cP46JG2a.jpg?0523"]


def test_extract_image_url_from_success_payload_variants() -> None:
    payload_list = {"status": "success", "output": ["https://img.example/list.png"]}
    payload_dict = {"status": "succeeded", "output": {"url": "https://img.example/dict.png"}}
    payload_nested = {"status": "done", "result": {"image_url": "https://img.example/nested.png"}}
    payload_missing = {"status": "completed", "output": []}

    assert ChadImageService._extract_image_url(payload_list) == "https://img.example/list.png"  # noqa: SLF001
    assert ChadImageService._extract_image_url(payload_dict) == "https://img.example/dict.png"  # noqa: SLF001
    assert ChadImageService._extract_image_url(payload_nested) == "https://img.example/nested.png"  # noqa: SLF001
    assert ChadImageService._extract_image_url(payload_missing) == ""  # noqa: SLF001


def test_fit_for_image_model_limits_prompt_length() -> None:
    long_prompt = ("Аниме портрет Сайны с деталями. " * 120).strip()
    fitted = ChadImageService._fit_for_image_model(long_prompt)  # noqa: SLF001
    assert len(fitted) <= ChadImageService._MAX_IMAGE_PROMPT_CHARS + 10  # noqa: SLF001
    assert "..." in fitted


@pytest.mark.asyncio
async def test_try_generate_uses_llm_caption_on_success() -> None:
    settings = make_settings()
    prompt_service = FakePromptService(ImagePromptService.GenerationMode.SIMPLE, simple_prompt="кот в очках")
    image_service = ChadImageService(settings, prompt_service=prompt_service, llm=FakeLLM("Получилось стильно, мне нравится этот вайб!"))

    async def fake_imagine(prompt: str, extra_images=None, reference_urls=None):  # noqa: ANN001, ARG001
        return "cid-1", ""

    async def fake_check(content_id: str):  # noqa: ARG001
        return {"status": "success", "output": ["https://img.example/generated.png"]}

    image_service._imagine = fake_imagine  # type: ignore[method-assign]  # noqa: SLF001
    image_service._check = fake_check  # type: ignore[method-assign]  # noqa: SLF001
    try:
        result = await image_service.try_generate("Сгенерируй кота в очках")
    finally:
        await image_service.close()

    assert result.success is True
    assert result.caption == "Получилось стильно, мне нравится этот вайб!"


@pytest.mark.asyncio
async def test_try_generate_falls_back_caption_when_llm_unavailable() -> None:
    settings = make_settings()
    prompt_service = FakePromptService(ImagePromptService.GenerationMode.SIMPLE, simple_prompt="кот в очках")
    image_service = ChadImageService(
        settings,
        prompt_service=prompt_service,
        llm=FakeLLM("Я на месте, но внешний AI сейчас недоступен."),
    )

    async def fake_imagine(prompt: str, extra_images=None, reference_urls=None):  # noqa: ANN001, ARG001
        return "cid-2", ""

    async def fake_check(content_id: str):  # noqa: ARG001
        return {"status": "success", "output": ["https://img.example/generated2.png"]}

    image_service._imagine = fake_imagine  # type: ignore[method-assign]  # noqa: SLF001
    image_service._check = fake_check  # type: ignore[method-assign]  # noqa: SLF001
    try:
        result = await image_service.try_generate("Сгенерируй кота в очках")
    finally:
        await image_service.close()

    assert result.success is True
    assert result.caption == "caption"
