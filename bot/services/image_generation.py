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

    @staticmethod
    def _cleanup_llm_prompt(raw: str) -> str:
        text = (raw or "").strip()
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1]).strip()
        text = re.sub(r"^\s*(промпт|prompt)\s*:\s*", "", text, flags=re.IGNORECASE)
        return text.strip()

    async def _build_final_prompt(self, user_text: str) -> str:
        base_prompt = self.prompt_service.build_base_prompt(user_text)
        if not base_prompt:
            return ""
        if not self.prompt_service.is_saina_request(user_text):
            return base_prompt

        messages = [
            {
                "role": "system",
                "content": (
                    "Ты редактор промптов для image-generation модели. "
                    "Собери один финальный промпт для realistic/anime portrait generation. "
                    "Сохраняй ключевую внешность Сайны и адаптируй сцену под запрос пользователя. "
                    "Верни только готовый промпт, без пояснений, без Markdown, без списка вариантов."
                ),
            },
            {"role": "user", "content": base_prompt},
        ]
        llm_text = await self.llm.complete(
            messages,
            max_tokens=10000,
            model=self.settings.chad_image_prompt_model,
            timeout_seconds=self.settings.chad_image_prompt_timeout_seconds,
        )
        cleaned = self._cleanup_llm_prompt(llm_text)
        if (
            not cleaned
            or len(cleaned) < 30
            or "не смог сформировать ответ" in cleaned.lower()
            or "внешний ai сейчас недоступен" in cleaned.lower()
            or "проверь `chad_ai_base_url`" in cleaned.lower()
        ):
            logger.warning("image_prompt_fallback_to_base_prompt raw=%r", llm_text[:220])
            return base_prompt
        return cleaned

    async def _imagine(self, prompt: str) -> tuple[str, str]:
        endpoint = f"/api/public/{self.settings.chad_image_model}/imagine"
        payload = {
            "api_key": self.settings.chad_ai_api_key,
            "prompt": prompt,
            "aspect_ratio": self.settings.chad_image_aspect_ratio,
        }
        logger.info("chad_image_imagine_request endpoint=%s model=%s prompt=%r", endpoint, self.settings.chad_image_model, prompt[:500])
        response = await self._client.post(endpoint, json=payload)
        response.raise_for_status()
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

    async def try_generate(self, user_text: str) -> ImageGenerationResult:
        if not self.is_image_request(user_text):
            return ImageGenerationResult(handled=False)

        prompt = await self._build_final_prompt(user_text)
        if not prompt:
            return ImageGenerationResult(
                handled=True,
                success=False,
                error_message="Не вижу, что именно рисовать. Дай короткое описание сцены.",
            )

        try:
            content_id, imagine_error = await self._imagine(prompt)
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
                if status == "completed":
                    output = status_payload.get("output") or []
                    if isinstance(output, list) and output:
                        image_url = str(output[0])
                        if image_url:
                            return ImageGenerationResult(
                                handled=True,
                                success=True,
                                image_url=image_url,
                                prompt=prompt,
                                caption=self.prompt_service.build_caption(user_text),
                            )
                    return ImageGenerationResult(handled=True, success=False, error_message="Картинка сгенерирована, но ссылка пустая.")
                if status in {"failed", "cancelled"}:
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
