from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot

from bot.config import Settings
from bot.services.chad_ai import ChadAIClient
from bot.services.memory import MemPalaceService, MemoryMessage
from bot.services.sheets import GoogleSheetsService
from bot.services.soul import SoulService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DigestRunResult:
    text: str
    messages_count: int
    orders_count: int
    facts_total: int
    facts_added: int


class DigestService:
    def __init__(
        self,
        *,
        settings: Settings,
        bot: Bot,
        llm: ChadAIClient,
        soul: SoulService,
        memory: MemPalaceService,
        sheets: GoogleSheetsService,
    ) -> None:
        self.settings = settings
        self.bot = bot
        self.llm = llm
        self.soul = soul
        self.memory = memory
        self.sheets = sheets

    def _window_bounds(
        self,
        window_hours: int,
        *,
        trigger: str = "manual",
        now: datetime | None = None,
    ) -> tuple[datetime, datetime]:
        """
        Return fixed digest-day window: from 23:00 previous day to 23:00 current day
        in bot timezone, converted to UTC for storage queries.
        """
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        else:
            current = current.astimezone(timezone.utc)

        try:
            local_tz = ZoneInfo(self.settings.timezone)
        except Exception:
            local_tz = timezone.utc

        current_local = current.astimezone(local_tz)
        anchor_local = current_local.replace(
            hour=self.settings.daily_digest_hour,
            minute=self.settings.daily_digest_minute,
            second=0,
            microsecond=0,
        )
        # Last scheduled digest boundary (23:00) that already happened.
        if current_local < anchor_local:
            anchor_local = anchor_local - timedelta(days=1)

        if trigger == "scheduled":
            end_local = anchor_local
            if window_hours and window_hours != 24:
                start_local = end_local - timedelta(hours=max(1, window_hours))
            else:
                start_local = end_local - timedelta(days=1)
        else:
            # Manual digest: from last 23:00 boundary until now.
            start_local = anchor_local
            end_local = current_local

        return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)

    @staticmethod
    def _format_order_brief(order: dict[str, object]) -> str:
        return (
            f"ID={order.get('order_id', '')}; "
            f"title={order.get('title', '')}; "
            f"client={order.get('client', '')}; "
            f"responsible={order.get('responsible', '')}; "
            f"due_date={order.get('due_date', '')}; "
            f"progress={order.get('progress_percent', 0)}%; "
            f"amount={order.get('amount', 0)}"
        )

    @staticmethod
    def _messages_to_prompt_chunk(messages: list[MemoryMessage], limit: int = 140) -> str:
        rows: list[str] = []
        for item in messages[-max(1, limit) :]:
            actor = f"user_{item.user_id}" if item.role == "user" else "saina"
            rows.append(f"{item.created_at} | chat={item.chat_id} | {actor}: {item.text[:400]}")
        return "\n".join(rows)

    @staticmethod
    def _has_required_blocks(text: str) -> bool:
        lowered = text.lower()
        workshop_ok = ("блок по мастерской" in lowered) or ("мастерская:" in lowered)
        chat_ok = ("блок по чату" in lowered) or ("чат:" in lowered)
        return workshop_ok and chat_ok

    @staticmethod
    def _wrap_into_required_blocks(text: str) -> str:
        body = text.strip() or "Содержательный итог не сформирован."
        return (
            "Блок по мастерской:\n"
            "- Ключевые рабочие события за сутки: " + body + "\n\n"
            "Блок по чату:\n"
            "- О чем говорили и к чему пришли: " + body
        )

    @staticmethod
    def _fallback_digest(messages_count: int, orders_count: int) -> str:
        return (
            "Блок по мастерской:\n"
            f"- Активных заказов сейчас: {orders_count}. "
            "Полноценную структурную выжимку не смогла собрать без внешнего AI.\n\n"
            "Блок по чату:\n"
            f"- Сообщений за окно: {messages_count}. "
            "Темы обсуждений и финальные договоренности нужно пересобрать после восстановления внешнего AI."
        )

    @staticmethod
    def _normalize_fact(text: str) -> str:
        lowered = text.lower().strip()
        lowered = re.sub(r"[^a-zа-яё0-9\s]+", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered)
        return lowered

    @staticmethod
    def _token_overlap(left: str, right: str) -> float:
        left_tokens = {token for token in left.split() if len(token) > 2}
        right_tokens = {token for token in right.split() if len(token) > 2}
        if not left_tokens or not right_tokens:
            return 0.0
        inter = len(left_tokens & right_tokens)
        return inter / max(1, len(left_tokens))

    @staticmethod
    def _parse_json_object(raw: str) -> dict[str, object] | None:
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
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    async def _extract_atomic_facts(self, digest_text: str) -> list[str]:
        messages = [
            {"role": "system", "content": self.soul.persona},
            {"role": "system", "content": self.soul.module_style_prompt("secretary")},
            {
                "role": "system",
                "content": (
                    "Выдели из дайджеста только проверяемые атомарные факты. "
                    "Верни строго JSON: {\"facts\":[\"...\"]}. "
                    "Без эмоций, без оценок, без повторов. Максимум 10 фактов."
                ),
            },
            {"role": "user", "content": digest_text},
        ]
        raw = await self.llm.complete(messages, timeout_seconds=10.0, max_tokens=100000)
        payload = self._parse_json_object(raw) or {}
        facts_raw = payload.get("facts", [])
        if not isinstance(facts_raw, list):
            return []
        deduped: list[str] = []
        seen: set[str] = set()
        for item in facts_raw:
            fact = str(item).strip()
            if not fact:
                continue
            key = self._normalize_fact(fact)
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(fact)
        return deduped[:10]

    async def _fact_exists_in_memory(self, fact: str, *, chat_id: int) -> bool:
        context = await self.memory.search_context(
            fact,
            user_id=None,
            limit=4,
            chat_id=chat_id,
            fallback_user_ids=[0],
        )
        if not context.strip():
            return False
        fact_norm = self._normalize_fact(fact)
        context_norm = self._normalize_fact(context)
        if fact_norm in context_norm:
            return True
        return self._token_overlap(fact_norm, context_norm) >= 0.75

    async def sync_digest_facts_to_memory(self, digest_text: str, *, chat_id: int) -> tuple[int, int]:
        facts = await self._extract_atomic_facts(digest_text)
        if not facts:
            return 0, 0
        added = 0
        seen_keys: set[str] = set()
        for fact in facts:
            key = self._normalize_fact(fact)
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            exists = await self._fact_exists_in_memory(fact, chat_id=chat_id)
            if exists:
                continue
            await self.memory.remember("assistant", user_id=0, chat_id=chat_id, text=f"[digest_sync] {fact}")
            added += 1
        logger.info("digest_memory_sync chat_id=%s facts_total=%s facts_added=%s", chat_id, len(facts), added)
        return len(facts), added

    async def build_digest(self, *, window_hours: int = 24, trigger: str = "manual") -> DigestRunResult:
        start, end = self._window_bounds(window_hours, trigger=trigger)
        messages = await self.memory.list_shared_messages_window(since=start, until=end, limit=500)
        orders = await self.sheets.list_active_orders()
        msg_chunk = self._messages_to_prompt_chunk(messages)
        order_chunk = "\n".join(self._format_order_brief(item) for item in orders) or "Нет активных заказов."

        if not messages and not orders:
            fallback = (
                "Блок по мастерской:\n"
                "- Значимых рабочих обновлений за последние 24 часа не зафиксировано.\n\n"
                "Блок по чату:\n"
                "- Обсуждений с итоговыми решениями за окно не найдено."
            )
            facts_total, facts_added = await self.sync_digest_facts_to_memory(
                fallback,
                chat_id=self.settings.digest_chat_id,
            )
            return DigestRunResult(
                text=fallback,
                messages_count=0,
                orders_count=0,
                facts_total=facts_total,
                facts_added=facts_added,
            )

        prompt_messages = [
            {"role": "system", "content": self.soul.persona},
            {"role": "system", "content": self.soul.module_style_prompt("secretary")},
            {
                "role": "system",
                "content": (
                    "Собери ежедневный дайджест за последние 24 часа. "
                    "Тон: факты + легкая ирония, без токсичности. "
                    "Критичный приоритет: прогресс/риски по заказам. "
                    "Не выдумывай события. Если данных мало, скажи это прямо. "
                    "Ответ строго раздели на 2 блока и не меняй их названия: "
                    "'Блок по мастерской' и 'Блок по чату'."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Триггер: {trigger}\n"
                    f"Окно: {start.isoformat()} - {end.isoformat()}\n\n"
                    f"Активные заказы:\n{order_chunk}\n\n"
                    f"Сообщения за окно:\n{msg_chunk or 'Нет сообщений в памяти за окно.'}\n\n"
                    "Формат ответа строго:\n"
                    "Блок по мастерской:\n"
                    "- что по заказам, срокам, прогрессу, рабочим решениям.\n\n"
                    "Блок по чату:\n"
                    "- о чем говорили, к каким договоренностям пришли, что осталось открытым."
                ),
            },
        ]
        raw = await self.llm.complete(prompt_messages, timeout_seconds=15.0, max_tokens=100000)
        digest_text = self.soul.finalize_reply(raw).strip()
        if not digest_text or "внешний ai сейчас недоступен" in digest_text.lower():
            digest_text = self._fallback_digest(messages_count=len(messages), orders_count=len(orders))
        elif not self._has_required_blocks(digest_text):
            digest_text = self._wrap_into_required_blocks(digest_text)
        facts_total, facts_added = await self.sync_digest_facts_to_memory(
            digest_text,
            chat_id=self.settings.digest_chat_id,
        )
        return DigestRunResult(
            text=digest_text,
            messages_count=len(messages),
            orders_count=len(orders),
            facts_total=facts_total,
            facts_added=facts_added,
        )

    async def send_daily_digest(self) -> None:
        if self.settings.digest_chat_id == 0:
            logger.warning("digest_skipped_missing_chat_id")
            return
        result = await self.build_digest(window_hours=24, trigger="scheduled")
        await self.bot.send_message(self.settings.digest_chat_id, result.text)
        logger.info(
            "digest_sent chat_id=%s messages=%s orders=%s facts_total=%s facts_added=%s",
            self.settings.digest_chat_id,
            result.messages_count,
            result.orders_count,
            result.facts_total,
            result.facts_added,
        )
