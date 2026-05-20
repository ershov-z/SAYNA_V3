from __future__ import annotations

import json
import logging
import re
from typing import Any

from bot.config import Settings
from bot.models.message_flow import MessageEnvelope, ModuleName, RouteDecision
from bot.services.chad_ai import ChadAIClient
from bot.services.soul import SoulService

logger = logging.getLogger(__name__)


class DecisionMakerService:
    def __init__(self, settings: Settings, llm: ChadAIClient, soul: SoulService) -> None:
        self.settings = settings
        self.llm = llm
        self.soul = soul

    @staticmethod
    def _clamp_confidence(value: object) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, parsed))

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any] | None:
        text = raw.strip()
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1]).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        candidate = text[start : end + 1]
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _normalize_module(value: object) -> ModuleName | None:
        raw = str(value or "").strip().lower()
        aliases = {
            "secretary": ModuleName.SECRETARY,
            "orders": ModuleName.SECRETARY,
            "tasks": ModuleName.SECRETARY,
            "generation": ModuleName.GENERATION,
            "image": ModuleName.GENERATION,
            "gen": ModuleName.GENERATION,
            "chat": ModuleName.CHAT,
            "dialogue": ModuleName.CHAT,
        }
        return aliases.get(raw)

    @staticmethod
    def _looks_like_secretary_intent(text: str) -> bool:
        lowered = (text or "").strip().lower()
        if not lowered:
            return False
        secretary_keywords = (
            "заказ",
            "дедлайн",
            "прогресс",
            "готовность",
            "поруч",
            "задач",
            "todo",
            "напомни",
            "попроси",
            "скажи",
            "кто должен",
            "через минут",
            "через час",
            "/orders",
            "/todos",
        )
        return any(keyword in lowered for keyword in secretary_keywords)

    @staticmethod
    def _looks_like_generation_intent(text: str) -> bool:
        lowered = (text or "").strip().lower()
        if not lowered:
            return False
        patterns = (
            r"\bсгенер\w*",
            r"\bнарис\w*",
            r"\bсоздай\s+(?:картин\w*|изображен\w*|арт|фото)\b",
            r"\bсделай\s+(?:картин\w*|арт|иллюстрац\w*|фото|селфи)\b",
            r"\bпокажи\s+себя\b",
            r"\bпокажи\s+как\s+ты\b",
            r"\bкак\s+ты\s+выглядишь\b",
            r"\bфотк\w*\b",
            r"\bаватар\w*\b",
            r"\bселфи\b",
        )
        return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in patterns)

    async def decide(
        self,
        envelope: MessageEnvelope,
        *,
        memory_context: str = "",
        recent_chat: list[dict[str, str]] | None = None,
    ) -> RouteDecision:
        history_lines = "\n".join(item.get("content", "") for item in (recent_chat or [])) or "history_empty"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.soul.persona},
            {
                "role": "system",
                "content": (
                    "Ты Decision Maker. Маршрутизируй каждое сообщение ровно в один модуль: "
                    "secretary (заказы/todos/рабочий CRUD), generation (просьбы сгенерировать изображение), "
                    "chat (обычный диалог, болтовня, неструктурированные вопросы). "
                    "Критично: любые просьбы напомнить/попросить другого участника что-то сделать, "
                    "включая форматы с относительным временем ('через минуту', 'через 2 часа'), "
                    "всегда относятся к secretary. "
                    "Любые сообщения про дедлайн, статус выполнения, поручения, задачи и списки /orders /todos — это secretary. "
                    "Generation выбирай только когда пользователь явно хочет создать/сгенерировать изображение. "
                    "Если сомневаешься между secretary и chat, выбирай secretary. "
                    "Верни только JSON формата "
                    '{"module":"secretary|generation|chat","confidence":0..1,"reason":"коротко"}.'
                ),
            },
            {
                "role": "system",
                "content": (
                    "Примеры secretary:\n"
                    "- 'через минуту напомни Кате закрыть дверь'\n"
                    "- 'попроси Софу сдать макет до завтра'\n"
                    "- 'что по заказам и дедлайнам'\n"
                    "- '/todos'"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"is_group_chat={envelope.is_group_chat}\n"
                    f"context_hint={envelope.context_hint}\n"
                    f"has_images={'yes' if envelope.images else 'no'}\n"
                    f"reply_to_text={envelope.reply_to_text[:300]}\n"
                    f"memory_context={memory_context[:700]}\n"
                    f"recent_chat={history_lines[:900]}\n"
                    f"text={envelope.text}"
                ),
            },
        ]
        raw = await self.llm.complete(
            messages=messages,
            model=self.settings.chad_decision_model,
            timeout_seconds=self.settings.chad_decision_timeout_seconds,
            max_tokens=120,
        )
        payload = self._extract_json(raw) or {}
        module = self._normalize_module(payload.get("module"))
        confidence = self._clamp_confidence(payload.get("confidence"))
        reason = str(payload.get("reason", "")).strip()
        fallback = False
        secretary_by_keyword = self._looks_like_secretary_intent(envelope.text)
        generation_by_keyword = self._looks_like_generation_intent(envelope.text)
        if module in {None, ModuleName.CHAT} and generation_by_keyword and not secretary_by_keyword:
            module = ModuleName.GENERATION
            fallback = True
            if not reason:
                reason = "fallback_generation_keyword"
        elif module is None or confidence < self.settings.chad_decision_min_confidence:
            if secretary_by_keyword:
                module = ModuleName.SECRETARY
            elif generation_by_keyword:
                module = ModuleName.GENERATION
            else:
                module = ModuleName.CHAT
            fallback = True
            if not reason:
                if secretary_by_keyword:
                    reason = "fallback_secretary_keyword"
                elif generation_by_keyword:
                    reason = "fallback_generation_keyword"
                else:
                    reason = "fallback_chat"
        decision = RouteDecision(
            module=module,
            confidence=confidence,
            reason=reason,
            raw=raw[:600],
            fallback_used=fallback,
        )
        logger.info(
            "decision_maker_result module=%s confidence=%.2f fallback=%s reason=%s text=%r",
            decision.module.value,
            decision.confidence,
            decision.fallback_used,
            decision.reason,
            envelope.text[:220],
        )
        return decision
