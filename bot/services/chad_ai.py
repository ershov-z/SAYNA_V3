from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from bot.config import Settings

logger = logging.getLogger(__name__)


class ChadAIClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        normalized_base_url = self._normalize_base_url(settings.chad_ai_base_url)
        parsed = urlparse(normalized_base_url)
        self._host = parsed.hostname or ""
        self._client = httpx.AsyncClient(
            base_url=normalized_base_url.rstrip("/"),
            timeout=45.0,
            headers={"Content-Type": "application/json"},
        )
        logger.info("chad_client_initialized base_url=%s", normalized_base_url)

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        """
        Keep backward compatibility with old /api/public configuration.
        New Chad text API is OpenAI-compatible under /api/v1.
        """
        stripped = (base_url or "").strip().rstrip("/")
        parsed = urlparse(stripped)
        host = parsed.hostname or ""
        if "ask.chadgpt.ru" in host and parsed.path.rstrip("/") == "/api/public":
            updated = parsed._replace(path="/api/v1")
            return urlunparse(updated)
        return stripped

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text_parts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
            return "\n".join(part for part in text_parts if part).strip()
        return str(content).strip()

    @staticmethod
    def _first_user_snippet(messages: list[dict[str, Any]]) -> str:
        for item in reversed(messages):
            if str(item.get("role", "")) == "user":
                text = ChadAIClient._content_to_text(item.get("content", ""))
                return text
        return ""

    @staticmethod
    def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
        masked = dict(payload)
        if "api_key" in masked:
            masked["api_key"] = "***"
        return masked

    def _prepare_openai_compatible_payload(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int,
        model: str,
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        payload = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {self._settings.chad_ai_api_key}"}
        return "/chat/completions", payload, headers

    @staticmethod
    def _strip_markdown_fence(text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 3:
                return "\n".join(lines[1:-1]).strip()
        return stripped

    @staticmethod
    def _parse_rerank_order(raw_text: str, size: int) -> list[int]:
        text = ChadAIClient._strip_markdown_fence(raw_text)
        candidates: list[int] = []
        parsed: Any = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None

        if isinstance(parsed, dict):
            for key in ("order", "indices", "ranking"):
                value = parsed.get(key)
                if isinstance(value, list):
                    candidates = [int(item) for item in value if isinstance(item, int | str) and str(item).strip().lstrip("-").isdigit()]
                    break
        elif isinstance(parsed, list):
            candidates = [int(item) for item in parsed if isinstance(item, int | str) and str(item).strip().lstrip("-").isdigit()]

        if not candidates:
            # Fallback parser for malformed but still salvageable outputs.
            digits = [int(chunk) for chunk in re.findall(r"\d+", text)]
            candidates = digits

        seen: set[int] = set()
        valid: list[int] = []
        for idx in candidates:
            if 0 <= idx < size and idx not in seen:
                seen.add(idx)
                valid.append(idx)
        for idx in range(size):
            if idx not in seen:
                valid.append(idx)
        return valid

    async def rerank_memory_candidates(
        self,
        *,
        query: str,
        candidates: list[str],
        timeout_seconds: float,
        top_k: int,
    ) -> list[int]:
        if not candidates:
            return []
        if len(candidates) == 1:
            return [0]
        logger.info(
            "chad_rerank_request model=%s timeout=%.2f candidates=%s top_k=%s query=%r",
            self._settings.chad_ai_model,
            timeout_seconds,
            len(candidates),
            top_k,
            query,
        )

        snippets = "\n".join(f"[{idx}] {text[:420]}" for idx, text in enumerate(candidates))
        rerank_messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "Ты ранжируешь фрагменты памяти по релевантности к запросу. "
                    "Ответь строго JSON-объектом формата "
                    '{"order":[индексы]} где индексы — целые числа, без комментариев.'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Запрос:\n{query}\n\n"
                    f"Фрагменты:\n{snippets}\n\n"
                    f"Верни top {top_k} самых релевантных индексов в порядке убывания релевантности."
                ),
            },
        ]
        try:
            endpoint, payload, headers = self._prepare_openai_compatible_payload(
                rerank_messages,
                max_tokens=100000,
                model=self._settings.chad_ai_model,
            )
            logger.info("chad_rerank_http_request endpoint=%s payload=%s", endpoint, payload)
            response = await self._client.post(endpoint, json=payload, headers=headers, timeout=timeout_seconds)
            response.raise_for_status()
            logger.info(
                "chad_rerank_network_ok endpoint=%s status=%s",
                endpoint,
                response.status_code,
            )
            data = response.json()
        except httpx.HTTPError as exc:
            logger.warning("chad_ai rerank request failed: %s", exc)
            return list(range(len(candidates)))

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = self._content_to_text(message.get("content"))
        if not text:
            logger.warning("chad_ai rerank returned empty content")
            return list(range(len(candidates)))

        ranked = self._parse_rerank_order(text, len(candidates))
        logger.info("chad_rerank_response ranked_count=%s raw=%r", len(ranked), text)
        return ranked[: max(1, min(top_k, len(candidates)))]

    async def complete(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 100000,
        *,
        model: str | None = None,
        timeout_seconds: float | None = None,
        images: list[str] | None = None,
    ) -> str:
        model_name = model or self._settings.chad_ai_model
        logger.info(
            "chad_complete_request model=%s timeout=%s max_tokens=%s messages=%s user_text=%r",
            model_name,
            timeout_seconds,
            max_tokens,
            len(messages),
            self._first_user_snippet(messages),
        )
        try:
            if images:
                logger.warning("images_are_not_supported_in_text_api images=%s", len(images))
            endpoint, payload, headers = self._prepare_openai_compatible_payload(messages, max_tokens, model_name)
            logger.info("chad_complete_http_request endpoint=%s payload=%s", endpoint, payload)
            response = await self._client.post(endpoint, json=payload, headers=headers, timeout=timeout_seconds)
            response.raise_for_status()
            logger.info(
                "chad_complete_network_ok endpoint=%s status=%s",
                endpoint,
                response.status_code,
            )
            data = response.json()
        except httpx.HTTPError as exc:
            logger.error("chad_ai request failed: %s", exc)
            return (
                "Я на месте, но внешний AI сейчас недоступен.\n"
                "Проверь `CHAD_AI_BASE_URL` (должен указывать на `/api/v1`) и API-ключ, потом повтори через минуту."
            )

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content")
        text = self._content_to_text(content)
        if text:
            logger.info("chad_complete_response_ok model=%s chars=%s response=%r", model_name, len(text), text)
            return text

        logger.warning("Unexpected content from chad_ai: %s", data)
        return "Не смог сформировать ответ, попробуй переформулировать вопрос."
