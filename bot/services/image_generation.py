from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from bot.config import Settings
from bot.services.chad_ai import ChadAIClient
from bot.services.image_prompt_service import ImagePromptService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ImageGenerationResult:
    handled: bool
    success: bool = False
    image_url: str = ""
    prompt: str = ""
    caption: str = ""
    error_message: str = ""


class ChadImageService:
    _SUCCESS_STATUSES = {"completed", "success", "succeeded", "done", "ready"}
    _FAILED_STATUSES = {"failed", "cancelled", "canceled", "error"}
    _MAX_IMAGE_PROMPT_CHARS = 1400

    def __init__(self, settings: Settings, prompt_service: ImagePromptService, llm: ChadAIClient) -> None:
        self.settings = settings
        self.prompt_service = prompt_service
        self.llm = llm
        self._client = httpx.AsyncClient(
            base_url=settings.chad_image_base_url.rstrip("/"),
            timeout=settings.chad_image_timeout_seconds,
            headers={"Content-Type": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    def is_image_request(self, text: str) -> bool:
        return self.prompt_service.is_image_request(text)

    @classmethod
    def _fit_for_image_model(cls, prompt: str) -> str:
        compact = re.sub(r"\s+", " ", prompt or "").strip()
        if len(compact) <= cls._MAX_IMAGE_PROMPT_CHARS:
            return compact
        # Keep both opening style constraints and closing scene request.
        head = compact[:1000].rstrip()
        tail = compact[-300:].lstrip()
        return f"{head} ... {tail}"

    async def _build_generated_caption(self, user_text: str, image_prompt: str) -> str:
        fallback = self.prompt_service.build_caption(user_text)
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты Сайна, AI-ассистент мастерской. "
                    "Сформируй короткий комментарий к уже сгенерированному изображению. "
                    "Пиши только по-русски, дружелюбно, от первого лица. "
                    "1-2 коротких предложения, без markdown, без списков, без кавычек вокруг ответа."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Запрос пользователя: {user_text}\n"
                    f"Промпт генерации: {image_prompt}\n"
                    "Напиши мой комментарий к итоговой картинке."
                ),
            },
        ]
        raw = await self.llm.complete(messages, max_tokens=220, timeout_seconds=6.0)
        caption = re.sub(r"\s+", " ", (raw or "").strip())
        if (
            not caption
            or "внешний ai сейчас недоступен" in caption.lower()
            or "не смог сформировать ответ" in caption.lower()
        ):
            return fallback
        if len(caption) > 280:
            caption = caption[:277].rstrip() + "..."
        return caption

    def _self_reference_url(self) -> str:
        if not self.settings.chad_image_self_reference_enabled:
            return ""
        reference_url = (self.settings.chad_image_self_reference_url or "").strip()
        if reference_url.startswith(("http://", "https://")):
            return reference_url
        if reference_url:
            logger.error("self_reference_url_invalid value=%s", reference_url)
        return ""

    def _prepare_user_images(self, user_images: list[str] | None) -> list[str]:
        images = [image for image in (user_images or []) if isinstance(image, str) and image.strip()]
        if self.settings.chad_image_extra_images_field != "image_urls":
            return images
        as_urls = [image for image in images if image.startswith(("http://", "https://"))]
        dropped = len(images) - len(as_urls)
        if dropped > 0:
            logger.warning("image_payload_skipped_non_url_images skipped=%s field=image_urls", dropped)
        return as_urls

    async def _build_generation_inputs(
        self,
        user_text: str,
        user_images: list[str] | None,
    ) -> tuple[str, list[str], list[str], str, bool]:
        mode = self.prompt_service.classify_generation_mode(user_text)
        if mode is ImagePromptService.GenerationMode.NONE:
            return "", [], [], "", False

        prompt = ""
        attachments = self._prepare_user_images(user_images)
        reference_urls: list[str] = []
        requires_reference = False
        if mode is ImagePromptService.GenerationMode.SELF:
            prompt = self.prompt_service.build_self_prompt(user_text)
            requires_reference = self.settings.chad_image_self_reference_required
            reference_url = self._self_reference_url()
            if reference_url:
                reference_urls = [reference_url]
            elif self.settings.chad_image_self_reference_enabled and requires_reference:
                return "", [], [], "Не нашла reference-ссылку Сайны для self-generation. Обнови CHAD_IMAGE_SELF_REFERENCE_URL.", True
        else:
            prompt = self.prompt_service.build_simple_prompt(user_text)

        return prompt, attachments, reference_urls, "", True

    async def _imagine(
        self,
        prompt: str,
        extra_images: list[str] | None = None,
        reference_urls: list[str] | None = None,
    ) -> tuple[str, str]:
        endpoint = f"/api/public/{self.settings.chad_image_model}/imagine"
        payload = {
            "api_key": self.settings.chad_ai_api_key,
            "prompt": prompt,
            "aspect_ratio": self.settings.chad_image_aspect_ratio,
        }
        if reference_urls:
            payload["image_urls"] = reference_urls
        if extra_images:
            payload[self.settings.chad_image_extra_images_field] = extra_images
        logger.info(
            "chad_image_imagine_request endpoint=%s model=%s prompt=%r images_count=%s image_field=%s refs_count=%s",
            endpoint,
            self.settings.chad_image_model,
            prompt[:500],
            len(extra_images or []),
            self.settings.chad_image_extra_images_field if extra_images else "-",
            len(reference_urls or []),
        )
        response = await self._client.post(endpoint, json=payload)
        if response.status_code >= 400:
            error_body = (response.text or "").strip()
            logger.error(
                "chad_image_imagine_http_error endpoint=%s status=%s body=%r",
                endpoint,
                response.status_code,
                error_body[:900],
            )
            return "", f"HTTP {response.status_code}: {error_body[:220]}"
        data = response.json()
        status = str(data.get("status", ""))
        if status == "failed":
            return "", str(data.get("error_message", "image-generation-failed"))
        return str(data.get("content_id", "")), ""

    async def _check(self, content_id: str) -> dict[str, Any]:
        payload = {"api_key": self.settings.chad_ai_api_key, "content_id": content_id}
        response = await self._client.post("/api/public/check", json=payload)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _extract_image_url(status_payload: dict[str, Any]) -> str:
        output = status_payload.get("output")
        if isinstance(output, list) and output:
            first = output[0]
            if isinstance(first, str):
                return first.strip()
            if isinstance(first, dict):
                for key in ("url", "image_url", "src"):
                    value = first.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        if isinstance(output, dict):
            for key in ("url", "image_url", "src"):
                value = output.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        # Some providers return nested payloads in result/data.
        for container_key in ("result", "data"):
            container = status_payload.get(container_key)
            if isinstance(container, dict):
                for key in ("url", "image_url", "src"):
                    value = container.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
                nested_output = container.get("output")
                if isinstance(nested_output, list) and nested_output:
                    first = nested_output[0]
                    if isinstance(first, str) and first.strip():
                        return first.strip()
                    if isinstance(first, dict):
                        for key in ("url", "image_url", "src"):
                            value = first.get(key)
                            if isinstance(value, str) and value.strip():
                                return value.strip()

        return ""

    async def try_generate(self, user_text: str, user_images: list[str] | None = None) -> ImageGenerationResult:
        if not self.is_image_request(user_text):
            return ImageGenerationResult(handled=False)

        prompt, attachments, reference_urls, build_error, handled = await self._build_generation_inputs(user_text, user_images)
        if not handled:
            return ImageGenerationResult(handled=False)
        if build_error:
            return ImageGenerationResult(handled=True, success=False, error_message=build_error)
        if not prompt:
            return ImageGenerationResult(
                handled=True,
                success=False,
                error_message="Не вижу, что именно рисовать. Дай короткое описание сцены.",
            )
        prompt = self._fit_for_image_model(prompt)

        try:
            content_id, imagine_error = await self._imagine(prompt, attachments, reference_urls)
            if imagine_error:
                return ImageGenerationResult(handled=True, success=False, error_message=f"Не смогла запустить генерацию: {imagine_error}")
            if not content_id:
                return ImageGenerationResult(handled=True, success=False, error_message="Генерация стартовала странно: не пришёл content_id.")

            elapsed = 0.0
            while elapsed < self.settings.chad_image_max_wait_seconds:
                await asyncio.sleep(self.settings.chad_image_check_interval_seconds)
                elapsed += self.settings.chad_image_check_interval_seconds
                status_payload = await self._check(content_id)
                status = str(status_payload.get("status", "")).lower()
                logger.info(
                    "chad_image_status content_id=%s status=%s elapsed=%.1fs",
                    content_id,
                    status or "unknown",
                    elapsed,
                )
                if status in self._SUCCESS_STATUSES:
                    image_url = self._extract_image_url(status_payload)
                    if image_url:
                        generated_caption = await self._build_generated_caption(user_text, prompt)
                        return ImageGenerationResult(
                            handled=True,
                            success=True,
                            image_url=image_url,
                            prompt=prompt,
                            caption=generated_caption,
                        )
                    return ImageGenerationResult(handled=True, success=False, error_message="Картинка сгенерирована, но ссылка пустая.")
                if status in self._FAILED_STATUSES:
                    return ImageGenerationResult(
                        handled=True,
                        success=False,
                        error_message=f"Генерация завершилась со статусом {status}: {status_payload.get('error_message', 'unknown-error')}",
                    )

            return ImageGenerationResult(
                handled=True,
                success=False,
                error_message="Генерация заняла слишком много времени. Попробуй ещё раз, я добью её со второй попытки.",
            )
        except httpx.HTTPError as exc:
            logger.error("chad_image_request_failed error=%s", exc)
            return ImageGenerationResult(
                handled=True,
                success=False,
                error_message="Сервис генерации картинок сейчас недоступен. Попробуй через минуту.",
            )
