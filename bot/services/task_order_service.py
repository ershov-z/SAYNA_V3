from __future__ import annotations

import json
import logging
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from aiogram import Bot

from bot.services.chad_ai import ChadAIClient
from bot.services.sheets import GoogleSheetsService
from bot.services.soul import SoulService

if TYPE_CHECKING:
    from bot.services.digest import DigestService

logger = logging.getLogger(__name__)

ORDER_PATTERN = re.compile(
    r"заказ(?:\s*[:\-])?\s+(?P<title>.+?)\s+сумма\s+(?P<amount>\d+(?:[.,]\d+)?)\s+дедлайн\s+(?P<date>\d{4}-\d{2}-\d{2})(?:\s+клиент\s+(?P<client>.+))?",
    flags=re.IGNORECASE,
)
ORDER_TITLE_PATTERN = re.compile(
    r"(?:взяли\s+)?заказ(?:\s+на)?(?:\s*[:\-])?\s+(?P<title>[^,\n]+)",
    flags=re.IGNORECASE,
)
AMOUNT_PATTERN = re.compile(r"(?:сумма|цена|стоимость|за)\s*(?P<amount>\d+(?:[.,]\d+)?)", flags=re.IGNORECASE)
CLIENT_PATTERN = re.compile(r"клиент[:\s]+(?P<client>[^\n,]+)", flags=re.IGNORECASE)
RESPONSIBLE_CREATE_PATTERN = re.compile(r"ответствен\w*\s*[:=\-]?\s*(?P<value>[^\n,.;]+)", flags=re.IGNORECASE)
ORDER_AMOUNT_EXPLICIT_PATTERN = re.compile(
    r"(?:сумма\s+заказа|стоимость\s+заказа|цена\s+заказа)\s*[:=\-]?\s*(?P<value>\d[\d\s.,]*(?:\s*(?:к|k|тыс\.?))?)",
    flags=re.IGNORECASE,
)
MATERIALS_AMOUNT_PATTERN = re.compile(
    r"(?:стоимость\s+материал\w*|сумма\s+материал\w*|материал\w*\s*[:=\-]?)\s*[:=\-]?\s*(?P<value>\d[\d\s.,]*(?:\s*(?:к|k|тыс\.?))?)",
    flags=re.IGNORECASE,
)
STORY_POINTS_PATTERN = re.compile(r"(?:сторипоинт\w*|story\s*points?)\s*[:=\-]?\s*(?P<value>\d+)", flags=re.IGNORECASE)
DEADLINE_PATTERN_ISO = re.compile(r"(?:(?:дедлайн|сдать|срок)\s*[:\-]?\s*)(?P<date>\d{4}-\d{2}-\d{2})", flags=re.IGNORECASE)
DEADLINE_PATTERN_DOT = re.compile(r"(?:(?:дедлайн|сдать|срок)\s*[:\-]?\s*)(?P<date>\d{1,2}\.\d{1,2}(?:\.\d{4})?)", flags=re.IGNORECASE)
DEADLINE_PATTERN_TEXT = re.compile(
    r"(?:(?:дедлайн|сдать|срок)\s*[:\-]?\s*)(?P<day>\d{1,2})\s+(?P<month>[а-яё]+)(?:\s+(?P<year>\d{4}))?",
    flags=re.IGNORECASE,
)
TODO_PATTERN = re.compile(
    r"user\s*(?P<to_user>\d+)\s+должен\s+(?P<text>.+?)(?:\s+до\s+(?P<date>\d{4}-\d{2}-\d{2}))?$",
    flags=re.IGNORECASE,
)
TODO_NATURAL_PATTERN = re.compile(
    r"(?:попроси|напомни|скажи)\s+(?P<to_user>[^\s,.:;!?]+)\s+(?P<text>.+?)(?:\s+до\s+(?P<date>[^.!?\n]+))?$",
    flags=re.IGNORECASE,
)
TODO_NATURAL_WITH_THAT_PATTERN = re.compile(
    r"(?:попроси|напомни|скажи)\s+(?P<to_user>[^\s,.:;!?]+)\s+(?:чтобы|что)\s+(?P<text>.+?)(?:\s+до\s+(?P<date>[^.!?\n]+))?$",
    flags=re.IGNORECASE,
)
TODO_RELATIVE_DUE_PATTERN = re.compile(
    r"через\s+(?:(?P<num>\d+)\s+)?(?P<unit>минуту|минуты|минут|мин|час|часа|часов|день|дня|дней)\b",
    flags=re.IGNORECASE,
)
PROGRESS_PATTERN = re.compile(
    r"прогресс\s+(?P<order_id>[a-z0-9]{8})\s+(?P<percent>\d{1,3})(?:\s+статус\s+(?P<status>\w+))?",
    flags=re.IGNORECASE,
)
PROGRESS_FLEX_PATTERN = re.compile(r"(?:прогресс|готовность)\s*[:\-]?\s*(?P<percent>\d{1,3})\s*%?", flags=re.IGNORECASE)
ORDER_REF_PATTERN = re.compile(r"(?:по\s+)?заказ(?:у|а|е|ом)?\s+(?P<ref>[^,\n.!?]+)", flags=re.IGNORECASE)
ORDER_ID_PATTERN = re.compile(r"\b(?P<order_id>[a-z0-9]{8})\b", flags=re.IGNORECASE)
ORDER_WORD_PATTERN = re.compile(r"\bзаказ\w*\b", flags=re.IGNORECASE)
PROGRESS_KEYWORDS_PATTERN = re.compile(r"\b(?:прогресс|готовность)\b", flags=re.IGNORECASE)
MUTATION_FIELD_KEYWORDS_PATTERN = re.compile(
    r"\b(?:ответствен\w*|клиент|прогресс\w*|готовност\w*|стоим\w*|сумм\w*|цен\w*|прайс|дедлайн|срок|процент\w*|отредакт\w*|обнов\w*|измени\w*|поменя\w*|назнач\w*)\b",
    flags=re.IGNORECASE,
)

ORDER_QUERY_STOPWORDS = {
    "что",
    "это",
    "эти",
    "какие",
    "какой",
    "какая",
    "каком",
    "по",
    "про",
    "за",
    "у",
    "нас",
    "в",
    "работе",
    "сейчас",
    "заказ",
    "заказы",
    "заказу",
    "заказов",
    "заказа",
}

MONTHS_RU = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}

MONTHS_RU_GEN = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}

TODO_USER_ALIASES: dict[str, int] = {
    "захар": 752142337,
    "захара": 752142337,
    "захару": 752142337,
    "zahar": 752142337,
    "fenptropillcosplay": 752142337,
    "катя": 495538754,
    "катю": 495538754,
    "кате": 495538754,
    "кати": 495538754,
    "katya": 495538754,
    "tenebriscosplay": 495538754,
    "софа": 381448542,
    "софе": 381448542,
    "софу": 381448542,
    "софой": 381448542,
    "софы": 381448542,
    "софочка": 381448542,
    "софушечка": 381448542,
    "sofa": 381448542,
    "sofia": 381448542,
    "salmosalar": 381448542,
}

TODO_SELF_REFERENCES = {"я", "мне", "меня", "мой", "моя", "моё", "мое"}
DIGEST_REQUEST_PATTERN = re.compile(
    r"(?:^/digest$|\bдайджест\b|\bсводк\w+\s+за\s+сут\w*\b|\bсобер[ио]\w*\s+дайджест\b)",
    flags=re.IGNORECASE,
)


@dataclass(slots=True)
class ParseResult:
    handled: bool
    text: str


@dataclass(slots=True)
class OrderMutation:
    order_id: str
    action: str
    field_name: str = ""
    value: str = ""


@dataclass(slots=True)
class PendingMutationPlan:
    source_text: str
    preview_text: str
    operations: list[OrderMutation]


@dataclass(slots=True)
class PendingCreateDecision:
    title: str
    client: str
    amount: float
    due_date: datetime
    responsible: str
    materials_amount: float
    progress_percent: int
    story_points: int
    candidate_order_ids: list[str]


@dataclass(slots=True)
class MutationPlanResult:
    preview: str
    action_type: str
    confidence: float
    ambiguity: str
    operations: list[OrderMutation]


@dataclass(slots=True)
class OrderSelectionResult:
    order_ids: list[str]
    confidence: float
    clarification: str


FIELD_ALIASES: dict[str, str] = {
    "title": "title",
    "name": "title",
    "название": "title",
    "название заказа": "title",
    "client": "client",
    "клиент": "client",
    "responsible": "responsible",
    "owner": "responsible",
    "assignee": "responsible",
    "ответственный": "responsible",
    "amount": "amount",
    "order_amount": "amount",
    "price": "amount",
    "сумма": "amount",
    "сумма заказа": "amount",
    "стоимость": "amount",
    "стоимость заказа": "amount",
    "цена": "amount",
    "прайс": "amount",
    "materials_amount": "materials_amount",
    "material_amount": "materials_amount",
    "стоимость материалов": "materials_amount",
    "сумма материалов": "materials_amount",
    "materials": "materials_amount",
    "due_date": "due_date",
    "deadline": "due_date",
    "дедлайн": "due_date",
    "срок": "due_date",
    "progress": "progress_percent",
    "progress_percent": "progress_percent",
    "готовность": "progress_percent",
    "прогресс": "progress_percent",
    "story_points": "story_points",
    "storypoints": "story_points",
    "story points": "story_points",
    "сторипоинты": "story_points",
    "status": "status",
    "статус": "status",
}


class TaskOrderService:
    def __init__(
        self,
        sheets: GoogleSheetsService,
        llm: ChadAIClient | None = None,
        soul: SoulService | None = None,
        bot: Bot | None = None,
        digest: "DigestService | None" = None,
    ) -> None:
        self.sheets = sheets
        self.llm = llm
        self.soul = soul
        self.bot = bot
        self.digest = digest
        self._last_order_by_user: dict[int, str] = {}
        self._pending_mutation_by_user: dict[int, PendingMutationPlan] = {}
        self._pending_create_by_user: dict[int, PendingCreateDecision] = {}
        self._order_dialogue_by_user: dict[int, deque[list[str]]] = {}
        self._user_label_by_id: dict[int, str] = {}
        self._persona = soul.persona if soul is not None else Path("bot/prompts/persona.md").read_text(encoding="utf-8")
        self._order_mutation_planner_prompt = Path("bot/prompts/order_mutation_planner.md").read_text(encoding="utf-8")

    @staticmethod
    def _normalize_name_token(value: object) -> str:
        cleaned = re.sub(r"[^a-zA-Zа-яА-ЯёЁ0-9_]+", "", str(value or "").lower())
        return cleaned

    def _resolve_todo_user_id(self, raw_value: str, from_user_id: int) -> int | None:
        value = str(raw_value or "").strip()
        if not value:
            return None
        if re.fullmatch(r"user\d+", value.lower()):
            return int(value.lower().removeprefix("user"))
        if value.startswith("@"):
            value = value[1:]
        normalized = self._normalize_name_token(value)
        if not normalized:
            return None
        if normalized in TODO_SELF_REFERENCES:
            return from_user_id
        if normalized.isdigit():
            return int(normalized)
        return TODO_USER_ALIASES.get(normalized)

    def _parse_todo_due_date(self, raw_value: str) -> datetime | None:
        value = str(raw_value or "").strip().lower().strip(".!?,;:")
        if not value:
            return None
        if value == "завтра":
            target = datetime.now(timezone.utc) + timedelta(days=1)
            return target.replace(hour=19, minute=0, second=0, microsecond=0)
        if value == "сегодня":
            target = datetime.now(timezone.utc)
            return target.replace(hour=23, minute=0, second=0, microsecond=0)
        try:
            parsed = datetime.fromisoformat(value.replace("z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
        parsed = self._parse_due_date(f"дедлайн {value}")
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _extract_relative_todo_due_date(self, text: str) -> datetime | None:
        match = TODO_RELATIVE_DUE_PATTERN.search(text)
        if not match:
            return None
        raw_num = str(match.group("num") or "").strip()
        unit = str(match.group("unit") or "").strip().lower()
        amount = int(raw_num) if raw_num.isdigit() else 1
        now = datetime.now(timezone.utc)
        if unit.startswith("мин"):
            return now + timedelta(minutes=amount)
        if unit.startswith("час"):
            return now + timedelta(hours=amount)
        if unit.startswith("д"):
            return now + timedelta(days=amount)
        return None

    async def _notify_todo_target(self, todo: dict[str, object]) -> None:
        if self.bot is None:
            return
        try:
            to_user = int(todo["to_user_id"])
            from_user = int(todo["from_user_id"])
            due_at_raw = str(todo.get("due_at", "")).strip()
            default_text = f"Новая просьба от user{from_user}: {todo['text']}" + (f"\nДедлайн: {due_at_raw}" if due_at_raw else "")
            text = default_text
            if self.llm is not None and self.soul is not None:
                messages = [
                    {"role": "system", "content": self.soul.persona},
                    {"role": "system", "content": self.soul.module_style_prompt("secretary")},
                    {
                        "role": "system",
                        "content": (
                            "Сформируй короткое личное сообщение о новой просьбе. "
                            "Укажи, кто попросил, что сделать и дедлайн (если есть). "
                            "Не выдумывай детали."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Кто попросил: user{from_user}\n"
                            f"Что сделать: {todo['text']}\n"
                            f"Дедлайн: {due_at_raw or 'не указан'}"
                        ),
                    },
                ]
                raw = await self.llm.complete(messages, max_tokens=100000, timeout_seconds=6.0)
                if "внешний ai сейчас недоступен" not in raw.lower():
                    maybe_text = self.soul.finalize_reply(raw).strip()
                    if maybe_text:
                        text = maybe_text
            await self.bot.send_message(to_user, text)
            mark_reminded = getattr(self.sheets, "mark_todo_reminded", None)
            if callable(mark_reminded):
                await mark_reminded(str(todo["todo_id"]))
        except Exception as exc:  # pragma: no cover - network/runtime guard
            logger.warning("failed_to_notify_todo_target todo_id=%s error=%s", todo.get("todo_id"), exc)

    @staticmethod
    def _is_self_reference(value: str) -> bool:
        lowered = value.strip().lower()
        return lowered in {"я", "я.", "меня", "мне", "мой", "моя", "моё", "мое"}

    def _resolve_self_reference(self, value: str, from_user_id: int, sender_display_name: str = "") -> str:
        cleaned = value.strip()
        if not self._is_self_reference(cleaned):
            return cleaned
        label = (sender_display_name or "").strip() or self._user_label_by_id.get(from_user_id, "").strip()
        if not label:
            label = f"user{from_user_id}"
        self._user_label_by_id[from_user_id] = label
        return label

    @staticmethod
    def _parse_money_value(raw_value: str) -> float | None:
        text = str(raw_value or "").strip().lower().replace(" ", "")
        if not text:
            return None
        multiplier = 1.0
        if text.endswith(("к", "k")):
            multiplier = 1000.0
            text = text[:-1]
        elif text.endswith("тыс"):
            multiplier = 1000.0
            text = text[:-3]
        elif text.endswith("тыс."):
            multiplier = 1000.0
            text = text[:-4]
        text = text.replace(",", ".")
        text = re.sub(r"[^0-9.]", "", text)
        if not text:
            return None
        try:
            return float(text) * multiplier
        except ValueError:
            return None

    def _parse_due_date(self, text: str) -> datetime | None:
        iso = DEADLINE_PATTERN_ISO.search(text)
        if iso:
            return datetime.fromisoformat(iso.group("date"))

        dotted = DEADLINE_PATTERN_DOT.search(text)
        if dotted:
            raw = dotted.group("date")
            parts = raw.split(".")
            if len(parts) == 2:
                day, month = map(int, parts)
                year = datetime.now().year
            else:
                day, month, year = map(int, parts)
            try:
                return datetime(year=year, month=month, day=day)
            except ValueError:
                return None

        texted = DEADLINE_PATTERN_TEXT.search(text)
        if texted:
            day = int(texted.group("day"))
            month_name = texted.group("month").lower()
            month = MONTHS_RU.get(month_name)
            if month is None:
                return None
            year = int(texted.group("year")) if texted.group("year") else datetime.now().year
            try:
                return datetime(year=year, month=month, day=day)
            except ValueError:
                return None
        return None

    def _parse_due_date_value(self, value: str) -> datetime | None:
        value = (value or "").strip()
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return self._parse_due_date(f"дедлайн {value}")

    @staticmethod
    def _clamp_confidence(value: object) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, parsed))

    @staticmethod
    def _is_destructive_operation(op: OrderMutation) -> bool:
        if op.action == "close_order":
            return True
        if op.action == "set_field" and op.field_name == "status":
            return op.value.strip().lower() in {"closed", "done", "cancelled"}
        if op.action == "set_status":
            return op.value.strip().lower() in {"closed", "done", "cancelled"}
        return False

    def _remember_order_context(self, from_user_id: int, orders: list[dict[str, object]]) -> None:
        if not orders:
            return
        ids = [str(order.get("order_id", "")).lower() for order in orders if str(order.get("order_id", "")).strip()]
        if not ids:
            return
        cache = self._order_dialogue_by_user.setdefault(from_user_id, deque(maxlen=6))
        cache.append(ids)
        self._last_order_by_user[from_user_id] = ids[0]

    def _resolve_contextual_orders(
        self,
        normalized: str,
        orders: list[dict[str, object]],
        from_user_id: int,
    ) -> list[dict[str, object]] | None:
        lowered = normalized.lower()
        if not any(token in lowered for token in ("перв", "втор", "трет", "последн", "этот", "его", "ее", "её")):
            return None
        history = self._order_dialogue_by_user.get(from_user_id)
        if not history:
            return None
        known_orders = {str(order.get("order_id", "")).lower(): order for order in orders}
        last_batch = history[-1] if history else []
        ordered_rows = [known_orders[order_id] for order_id in last_batch if order_id in known_orders]
        if not ordered_rows:
            return None
        index_map = {
            "перв": 0,
            "втор": 1,
            "трет": 2,
            "последн": -1,
        }
        for token, idx in index_map.items():
            if token in lowered:
                if idx >= 0 and idx < len(ordered_rows):
                    return [ordered_rows[idx]]
                if idx == -1:
                    return [ordered_rows[-1]]
        if any(token in lowered for token in ("этот", "его", "ее", "её")):
            last_id = self._last_order_by_user.get(from_user_id, "").lower()
            if last_id and last_id in known_orders:
                return [known_orders[last_id]]
            return [ordered_rows[0]]
        return None

    async def _try_handle_flexible_order(
        self,
        normalized: str,
        from_user_id: int,
        orders_snapshot: list[dict[str, object]],
        sender_display_name: str = "",
    ) -> ParseResult | None:
        lowered = normalized.lower()
        has_order_signal = any(token in lowered for token in ("заказ", "дедлайн", "сдать", "клиент", "цена", "сумма"))
        if not has_order_signal:
            return None

        title_match = ORDER_TITLE_PATTERN.search(normalized)
        explicit_order_amount_match = ORDER_AMOUNT_EXPLICIT_PATTERN.search(normalized)
        amount_match = explicit_order_amount_match or AMOUNT_PATTERN.search(normalized)
        materials_match = MATERIALS_AMOUNT_PATTERN.search(normalized)
        client_match = CLIENT_PATTERN.search(normalized)
        responsible_match = RESPONSIBLE_CREATE_PATTERN.search(normalized)
        story_points_match = STORY_POINTS_PATTERN.search(normalized)
        due_date = self._parse_due_date(normalized)
        progress_hint = PROGRESS_FLEX_PATTERN.search(normalized)

        title = title_match.group("title").strip() if title_match else ""
        amount_raw = (
            amount_match.group("value")
            if amount_match and "value" in amount_match.groupdict()
            else (amount_match.group("amount") if amount_match else "")
        )
        amount = self._parse_money_value(amount_raw) or 0.0
        materials_amount = self._parse_money_value(materials_match.group("value")) if materials_match else None
        client = client_match.group("client").strip() if client_match else "unknown"
        responsible = responsible_match.group("value").strip() if responsible_match else ""
        if responsible:
            responsible = self._resolve_self_reference(responsible, from_user_id, sender_display_name=sender_display_name)
        story_points = int(story_points_match.group("value")) if story_points_match else 0
        progress_percent = max(0, min(100, int(progress_hint.group("percent")))) if progress_hint else 0

        if title and due_date:
            return await self._create_or_ask_duplicate_confirmation(
                from_user_id=from_user_id,
                title=title,
                client=client,
                amount=amount,
                due_date=due_date,
                responsible=responsible,
                materials_amount=materials_amount or 0.0,
                progress_percent=progress_percent,
                story_points=story_points,
                orders_snapshot=orders_snapshot,
                success_message_template="Заказ добавлен: {order[title]} (ID {order[order_id]}).",
            )

        missing = []
        if not title:
            missing.append("название заказа")
        if due_date is None:
            missing.append("дедлайн")
        if missing:
            return ParseResult(
                True,
                "Вижу, что речь о заказе, но не хватает данных: "
                + ", ".join(missing)
                + ". Напиши в формате: `заказ <название> сумма <число> дедлайн YYYY-MM-DD клиент <имя>`",
            )
        return None

    @staticmethod
    def _is_orders_list_query(normalized: str) -> bool:
        lowered = normalized.lower()
        if lowered in {"список заказов", "/orders"}:
            return True
        return any(
            phrase in lowered
            for phrase in (
                "какие заказы",
                "какие у нас заказы",
                "покажи заказы",
                "покажи все заказы",
                "что по заказам",
                "что у нас по заказам",
                "что это за заказы",
                "что за заказы",
                "какие это заказы",
                "какие есть заказы",
                "все заказы",
                "список заказ",
            )
        )

    @staticmethod
    def _extract_order_reference(normalized: str) -> str | None:
        lowered = normalized.lower()
        if "заказ" not in lowered:
            return None
        id_match = ORDER_ID_PATTERN.search(normalized)
        if id_match:
            return id_match.group("order_id").lower()
        ref_match = ORDER_REF_PATTERN.search(normalized)
        if not ref_match:
            return None
        reference = ref_match.group("ref").strip().strip("\"'`").strip()
        reference = reference.removeprefix("№").removeprefix("#").strip()
        reference = re.sub(r"^(для|по)\s+", "", reference, flags=re.IGNORECASE).strip()
        if not reference:
            return None
        return reference

    @staticmethod
    def _looks_like_order_creation_intent(normalized: str) -> bool:
        lowered = normalized.lower()
        if "заказ" not in lowered:
            return False
        if any(
            token in lowered
            for token in (
                "удали",
                "удалить",
                "закрой",
                "закрыть",
                "переимен",
                "измени",
                "обнови",
                "поменяй",
                "поставь",
                "отредакт",
                "редакт",
            )
        ):
            return False
        if re.search(r"\bзаказ\s*[:\-]\s*", lowered) and (
            ("дедлайн" in lowered or "срок" in lowered or "сдать" in lowered)
            and ("сумма" in lowered or "цена" in lowered or "стоимость" in lowered or "клиент" in lowered)
        ):
            return True
        return any(
            token in lowered
            for token in (
                "заведи заказ",
                "создай заказ",
                "добавь заказ",
                "новый заказ",
                "взяли заказ",
            )
        )

    @staticmethod
    def _looks_like_order_mutation_intent(normalized: str) -> bool:
        lowered = normalized.lower()
        if any(token in lowered for token in ("удали", "удалить", "закрой", "закрыть", "переимен", "название", "назови")):
            return "заказ" in lowered
        if MUTATION_FIELD_KEYWORDS_PATTERN.search(lowered):
            return True
        return "заказ" in lowered and any(
            token in lowered
            for token in (
                "поставь",
                "измени",
                "обнови",
                "поменяй",
                "ответствен",
                "прогресс",
                "готовност",
                "стоим",
                "цена",
                "сумма",
                "дедлайн",
            )
        )

    @staticmethod
    def _is_positive_confirmation(text: str) -> bool:
        lowered = text.strip().lower()
        return lowered in {
            "да",
            "ага",
            "ок",
            "окей",
            "подтверждаю",
            "подтверждаю.",
            "давай",
            "подтверди",
        }

    @staticmethod
    def _is_negative_confirmation(text: str) -> bool:
        lowered = text.strip().lower()
        return lowered in {"нет", "отмена", "отменить", "стоп", "не надо"}

    @staticmethod
    def _normalize_for_match(value: str) -> str:
        return re.sub(r"[^a-zа-яё0-9]+", " ", str(value or "").lower()).strip()

    def _find_create_duplicates(
        self,
        *,
        title: str,
        client: str,
        orders: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        target_title = self._normalize_for_match(title)
        target_client = self._normalize_for_match(client)
        has_client = bool(target_client and target_client != "unknown")
        if not target_title:
            return []
        exact_title = [
            order
            for order in orders
            if self._normalize_for_match(str(order.get("title", ""))) == target_title
        ]
        if not exact_title:
            return []
        if not has_client:
            return exact_title
        same_client = [
            order
            for order in exact_title
            if self._normalize_for_match(str(order.get("client", ""))) == target_client
        ]
        return same_client or exact_title

    async def _create_or_ask_duplicate_confirmation(
        self,
        *,
        from_user_id: int,
        title: str,
        client: str,
        amount: float,
        due_date: datetime,
        responsible: str,
        materials_amount: float,
        progress_percent: int,
        story_points: int,
        orders_snapshot: list[dict[str, object]],
        success_message_template: str,
    ) -> ParseResult:
        duplicates = self._find_create_duplicates(
            title=title,
            client=client,
            orders=orders_snapshot,
        )
        if duplicates:
            candidate_ids = [str(order.get("order_id", "")).lower() for order in duplicates if str(order.get("order_id", "")).strip()]
            self._pending_create_by_user[from_user_id] = PendingCreateDecision(
                title=title,
                client=client,
                amount=amount,
                due_date=due_date,
                responsible=responsible,
                materials_amount=materials_amount,
                progress_percent=progress_percent,
                story_points=story_points,
                candidate_order_ids=candidate_ids,
            )
            examples = "; ".join(self._format_order_line(order) for order in duplicates[:2])
            return ParseResult(
                True,
                "Похоже, такой заказ уже есть. "
                + ("Нашла: " + examples + ". " if examples else "")
                + "Это новый заказ или обновляем существующий? Ответь `новый` или `старый`.",
            )
        order = await self.sheets.add_order(
            title=title,
            client=client,
            amount=amount,
            owner_user_id=from_user_id,
            due_date=due_date,
            responsible=responsible,
            materials_amount=materials_amount,
            progress_percent=progress_percent,
            story_points=story_points,
        )
        self._last_order_by_user[from_user_id] = str(order["order_id"]).lower()
        return ParseResult(
            True,
            success_message_template.format(
                order=order,
                due_date=due_date,
                due_date_iso=due_date.date().isoformat(),
                client=client,
            ),
        )

    async def _resolve_pending_create_choice(
        self,
        *,
        normalized: str,
        from_user_id: int,
        orders_snapshot: list[dict[str, object]],
    ) -> ParseResult | None:
        pending = self._pending_create_by_user.get(from_user_id)
        if pending is None:
            return None
        lowered = normalized.lower().strip()
        if not lowered:
            return ParseResult(True, "Нужно решение: создаём `новый` заказ или обновляем `старый`?")
        if lowered in {"новый", "новый заказ", "создай новый", "новый, создай"}:
            self._pending_create_by_user.pop(from_user_id, None)
            order = await self.sheets.add_order(
                title=pending.title,
                client=pending.client,
                amount=pending.amount,
                owner_user_id=from_user_id,
                due_date=pending.due_date,
                responsible=pending.responsible,
                materials_amount=pending.materials_amount,
                progress_percent=pending.progress_percent,
                story_points=pending.story_points,
            )
            self._last_order_by_user[from_user_id] = str(order["order_id"]).lower()
            return ParseResult(True, f"Ок, создала новый заказ {order['title']} (ID {order['order_id']}).")
        if lowered in {"старый", "старый заказ", "существующий", "обнови существующий", "обнови старый"}:
            candidate_ids = set(pending.candidate_order_ids)
            candidates = [
                order
                for order in orders_snapshot
                if str(order.get("order_id", "")).lower() in candidate_ids
            ]
            if not candidates:
                self._pending_create_by_user.pop(from_user_id, None)
                return ParseResult(True, "Не нашла старый заказ для обновления, давай заново опишем заказ.")
            target = candidates[0]
            target_id = str(target.get("order_id", "")).lower()
            client_for_update = pending.client if pending.client.strip().lower() != "unknown" else None
            ok = await self.sheets.update_order_fields(
                target_id,
                title=pending.title,
                client=client_for_update,
                responsible=pending.responsible or None,
                amount=pending.amount,
                materials_amount=pending.materials_amount,
                progress_percent=pending.progress_percent,
                story_points=pending.story_points,
                due_date=pending.due_date,
            )
            self._pending_create_by_user.pop(from_user_id, None)
            if not ok:
                return ParseResult(True, f"Не получилось обновить существующий заказ {target_id}.")
            self._last_order_by_user[from_user_id] = target_id
            return ParseResult(True, f"Ок, обновила существующий заказ {target_id} по новым данным.")
        return ParseResult(True, "Не поймала выбор. Напиши одним словом: `новый` или `старый`.")

    @staticmethod
    def _looks_like_order_query(normalized: str) -> bool:
        lowered = normalized.lower()
        if "/orders" in lowered:
            return True
        if ORDER_WORD_PATTERN.search(lowered):
            return True
        return "дедлайн" in lowered and "заказ" in lowered

    @staticmethod
    def _query_tokens(normalized: str) -> list[str]:
        tokens = re.findall(r"[a-zа-яё0-9]{3,}", normalized.lower())
        return [token for token in tokens if token not in ORDER_QUERY_STOPWORDS]

    @staticmethod
    def _format_order_line(order: dict[str, object]) -> str:
        order_id = str(order.get("order_id", "unknown"))
        title = str(order.get("title", "")).strip() or "без названия"
        client = str(order.get("client", "")).strip() or "не указан"
        responsible = str(order.get("responsible", "")).strip() or "не указан"
        due_date = str(order.get("due_date", ""))
        progress = order.get("progress_percent", 0)
        return (
            f"{title}: клиент {client}, ответственный {responsible}, "
            f"готовность {progress}%, дедлайн {due_date} (ID {order_id})"
        )

    @staticmethod
    def _format_order_details(order: dict[str, object]) -> str:
        order_id = str(order.get("order_id", "unknown"))
        title = str(order.get("title", "")).strip() or "без названия"
        client = str(order.get("client", "")).strip() or "не указан"
        responsible = str(order.get("responsible", "")).strip() or "не указан"
        amount = order.get("amount", "")
        materials_amount = order.get("materials_amount", "")
        story_points = order.get("story_points", 0)
        due_date = str(order.get("due_date", ""))
        status = str(order.get("status", "active"))
        progress = order.get("progress_percent", 0)
        return (
            f"По заказу {title} картина такая: клиент {client}, ответственный {responsible}, "
            f"готовность {progress}%, дедлайн {due_date}, сторипоинты {story_points}, "
            f"стоимость заказа {amount}, стоимость материалов {materials_amount}, статус {status}. ID {order_id}."
        )

    @staticmethod
    def _format_orders_digest(orders: list[dict[str, object]]) -> str:
        rows = [TaskOrderService._format_order_line(o) for o in orders]
        if len(rows) == 1:
            return "Сейчас активен один заказ: " + rows[0] + "."
        return (
            f"Сейчас в работе {len(rows)} заказа. "
            + " | ".join(rows)
            + ". Если нужно, разложу любой отдельно."
        )

    @staticmethod
    def _order_search_blob(order: dict[str, object]) -> str:
        title = str(order.get("title", "")).lower()
        client = str(order.get("client", "")).lower()
        responsible = str(order.get("responsible", "")).lower()
        order_id = str(order.get("order_id", "")).lower()
        return f"{title} {client} {responsible} {order_id}"

    @staticmethod
    def _order_compact(order: dict[str, object]) -> str:
        return (
            f"ID={order.get('order_id','')}; "
            f"title={order.get('title','')}; "
            f"client={order.get('client','')}; "
            f"responsible={order.get('responsible','')}; "
            f"due_date={order.get('due_date','')}; "
            f"amount={order.get('amount','')}; "
            f"materials_amount={order.get('materials_amount','')}; "
            f"story_points={order.get('story_points',0)}; "
            f"status={order.get('status','')}; "
            f"progress={order.get('progress_percent',0)}"
        )

    def _order_catalog_for_llm(self, orders: list[dict[str, object]], from_user_id: int) -> str:
        lines: list[str] = []
        for idx, order in enumerate(orders, start=1):
            aliases = [f"{idx}-й", f"заказ {idx}", "первый" if idx == 1 else "", "второй" if idx == 2 else "", "третий" if idx == 3 else ""]
            alias_text = ", ".join(item for item in aliases if item)
            lines.append(
                f"{idx}) order_id={order.get('order_id','')}; "
                f"title={order.get('title','')}; client={order.get('client','')}; "
                f"due_date={order.get('due_date','')}; amount={order.get('amount','')}; "
                f"progress={order.get('progress_percent', 0)}; aliases=[{alias_text}]"
            )
        last_order = self._last_order_by_user.get(from_user_id, "")
        return "\n".join(lines) + f"\nlast_referenced_order_id={last_order or 'none'}"

    async def _select_orders_with_llm(
        self,
        *,
        query: str,
        orders: list[dict[str, object]],
        from_user_id: int,
        memory_context: str = "",
    ) -> OrderSelectionResult:
        if self.llm is None or not orders:
            return OrderSelectionResult(order_ids=[], confidence=0.0, clarification="")
        order_dump = self._order_catalog_for_llm(orders, from_user_id)
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты определяешь, о каких заказах речь в пользовательском запросе. "
                    "Верни только JSON формата "
                    '{"target_order_ids":["id"],"confidence":0.0,"clarification":"если нужно уточнение"}. '
                    "Используй только order_id из каталога. Если подходит несколько заказов — верни все."
                ),
            },
            {"role": "system", "content": self._persona},
            {
                "role": "user",
                "content": (
                    f"Запрос: {query}\n\n"
                    f"Релевантная память:\n{memory_context[:700] or 'нет'}\n\n"
                    f"Каталог заказов:\n{order_dump}"
                ),
            },
        ]
        raw = await self.llm.complete(messages, max_tokens=100000, timeout_seconds=8.0)
        payload = self._parse_json_from_text(raw) or {}
        target_ids_raw = payload.get("target_order_ids", [])
        if not isinstance(target_ids_raw, list):
            target_ids_raw = []
        valid_ids = {str(order.get("order_id", "")).lower() for order in orders}
        order_ids = [str(item).lower().strip() for item in target_ids_raw if str(item).lower().strip() in valid_ids]
        confidence = self._clamp_confidence(payload.get("confidence"))
        clarification = str(payload.get("clarification", "")).strip()
        return OrderSelectionResult(order_ids=order_ids, confidence=confidence, clarification=clarification)

    @staticmethod
    def _parse_json_from_text(text: str) -> dict[str, object] | None:
        stripped = text.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 3:
                stripped = "\n".join(lines[1:-1]).strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        candidate = stripped[start : end + 1]
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return payload
        return None

    @staticmethod
    def _normalize_field_name(raw_field_name: str) -> str | None:
        key = str(raw_field_name or "").strip().lower()
        if not key:
            return None
        return FIELD_ALIASES.get(key)

    def _coerce_operations(
        self,
        raw_ops: object,
        orders: list[dict[str, object]],
    ) -> list[OrderMutation]:
        if not isinstance(raw_ops, list):
            return []
        valid_ids = {str(order.get("order_id", "")).lower() for order in orders}
        operations: list[OrderMutation] = []
        for item in raw_ops:
            if not isinstance(item, dict):
                continue
            order_id = str(item.get("order_id", "")).lower().strip()
            action = str(item.get("action", "")).strip()
            field_name_raw = str(item.get("field_name", item.get("field", item.get("column", "")))).strip()
            value = str(item.get("value", "")).strip()
            if not order_id or order_id not in valid_ids:
                continue
            field_name = self._normalize_field_name(field_name_raw) or ""
            if action == "set_deadline":
                action = "set_due_date"
            if action == "set_price":
                action = "set_amount"
            if action in {"set_materials", "set_materials_price", "set_materials_cost"}:
                action = "set_materials_amount"
            action_to_field = {
                "set_title": "title",
                "set_client": "responsible",
                "set_due_date": "due_date",
                "set_amount": "amount",
                "set_materials_amount": "materials_amount",
                "set_progress": "progress_percent",
                "set_story_points": "story_points",
                "set_status": "status",
            }
            if action == "close_order":
                operations.append(OrderMutation(order_id=order_id, action="close_order", value=value))
                continue
            if action in {"set_field", "update_field", "change_field"}:
                if not field_name:
                    continue
                operations.append(OrderMutation(order_id=order_id, action="set_field", field_name=field_name, value=value))
                continue
            mapped_field = action_to_field.get(action)
            if not mapped_field:
                continue
            operations.append(OrderMutation(order_id=order_id, action="set_field", field_name=mapped_field, value=value))
        return operations

    def _validate_operations(
        self,
        operations: list[OrderMutation],
    ) -> list[OrderMutation]:
        validated: list[OrderMutation] = []
        for op in operations:
            if op.action == "close_order":
                validated.append(op)
                continue
            if op.action != "set_field" or not op.field_name:
                continue
            if op.field_name in {"title", "client", "responsible", "status"} and not op.value.strip():
                continue
            if op.field_name == "due_date" and self._parse_due_date_value(op.value) is None:
                continue
            if op.field_name in {"amount", "materials_amount"}:
                try:
                    float(op.value.replace(",", ".").strip())
                except ValueError:
                    continue
            if op.field_name == "progress_percent":
                try:
                    int(float(op.value.replace("%", "").replace(",", ".").strip()))
                except ValueError:
                    continue
            if op.field_name == "story_points":
                try:
                    int(float(op.value.replace(",", ".").strip()))
                except ValueError:
                    continue
            validated.append(op)
        return validated

    def _dedupe_operations(self, operations: list[OrderMutation]) -> list[OrderMutation]:
        deduped: dict[tuple[str, str], OrderMutation] = {}
        for op in operations:
            deduped[(op.order_id, op.action, op.field_name)] = op
        return list(deduped.values())

    async def _plan_mutation_with_llm(
        self,
        *,
        query: str,
        orders: list[dict[str, object]],
        from_user_id: int,
        memory_context: str = "",
    ) -> MutationPlanResult:
        if self.llm is None:
            return MutationPlanResult(preview="", action_type="update", confidence=0.0, ambiguity="", operations=[])
        order_dump = self._order_catalog_for_llm(orders, from_user_id)
        messages = [
            {
                "role": "system",
                "content": self._order_mutation_planner_prompt,
            },
            {
                "role": "system",
                "content": self._persona,
            },
            {
                "role": "system",
                "content": self.soul.module_style_prompt("secretary")
                if self.soul is not None
                else "Держи дружелюбный живой тон без потери фактов.",
            },
            {
                "role": "user",
                "content": (
                    f"Запрос: {query}\n\n"
                    f"Релевантная память:\n{memory_context[:900] or 'нет'}\n\n"
                    f"Каталог активных заказов:\n{order_dump}"
                ),
            },
        ]
        raw = await self.llm.complete(messages, max_tokens=100000, timeout_seconds=10.0)
        payload = self._parse_json_from_text(raw)
        if not payload:
            return MutationPlanResult(preview="", action_type="update", confidence=0.0, ambiguity="", operations=[])
        preview = str(payload.get("preview", "")).strip()
        action_type = str(payload.get("action_type", "update")).strip().lower() or "update"
        ambiguity = str(payload.get("ambiguity", "")).strip()
        confidence = self._clamp_confidence(payload.get("confidence"))
        operations = self._validate_operations(self._coerce_operations(payload.get("operations"), orders))
        return MutationPlanResult(
            preview=preview,
            action_type=action_type,
            confidence=confidence,
            ambiguity=ambiguity,
            operations=operations,
        )

    async def _apply_mutation_operations(
        self,
        operations: list[OrderMutation],
        *,
        from_user_id: int | None = None,
        sender_display_name: str = "",
    ) -> list[str]:
        logs: list[str] = []
        field_label = {
            "title": "название",
            "client": "клиента",
            "responsible": "ответственного",
            "due_date": "дедлайн",
            "amount": "сумму заказа",
            "materials_amount": "стоимость материалов",
            "progress_percent": "прогресс",
            "story_points": "сторипоинты",
            "status": "статус",
        }
        for op in operations:
            if op.action == "close_order":
                ok = await self.sheets.close_order(op.order_id)
                logs.append(f"удалила заказ {op.order_id}" if ok else f"не смогла удалить заказ {op.order_id}")
                continue
            if op.action != "set_field":
                continue
            if op.field_name == "title":
                ok = await self.sheets.update_order_fields(op.order_id, title=op.value)
                logs.append(f"обновила название заказа {op.order_id} на «{op.value}»" if ok else f"не смогла обновить название заказа {op.order_id}")
                continue
            if op.field_name == "client":
                value = op.value
                if from_user_id is not None:
                    value = self._resolve_self_reference(value, from_user_id, sender_display_name=sender_display_name)
                ok = await self.sheets.update_order_fields(op.order_id, client=value)
                logs.append(f"обновила клиента заказа {op.order_id} на «{value}»" if ok else f"не смогла обновить клиента заказа {op.order_id}")
                continue
            if op.field_name == "responsible":
                value = op.value
                if from_user_id is not None:
                    value = self._resolve_self_reference(value, from_user_id, sender_display_name=sender_display_name)
                ok = await self.sheets.update_order_fields(op.order_id, responsible=value)
                logs.append(f"обновила ответственного заказа {op.order_id} на «{value}»" if ok else f"не смогла обновить ответственного заказа {op.order_id}")
                continue
            if op.field_name == "due_date":
                due_date = self._parse_due_date_value(op.value)
                if due_date is None:
                    logs.append(f"не смогла распознать дедлайн для заказа {op.order_id}: {op.value}")
                    continue
                ok = await self.sheets.update_order_fields(op.order_id, due_date=due_date)
                logs.append(f"обновила дедлайн заказа {op.order_id} на {due_date.date().isoformat()}" if ok else f"не смогла обновить дедлайн заказа {op.order_id}")
                continue
            if op.field_name in {"amount", "materials_amount"}:
                try:
                    parsed_amount = float(op.value.replace(",", ".").strip())
                except ValueError:
                    logs.append(f"не смогла распознать значение поля {field_label.get(op.field_name, op.field_name)} для заказа {op.order_id}: {op.value}")
                    continue
                if op.field_name == "amount":
                    ok = await self.sheets.update_order_fields(op.order_id, amount=parsed_amount)
                    logs.append(
                        f"обновила сумму заказа {op.order_id} на {parsed_amount:g}"
                        if ok
                        else f"не смогла обновить сумму заказа {op.order_id}"
                    )
                else:
                    ok = await self.sheets.update_order_fields(op.order_id, materials_amount=parsed_amount)
                    logs.append(
                        f"обновила стоимость материалов заказа {op.order_id} на {parsed_amount:g}"
                        if ok
                        else f"не смогла обновить стоимость материалов заказа {op.order_id}"
                    )
                continue
            if op.field_name == "progress_percent":
                try:
                    percent = int(float(op.value.replace("%", "").replace(",", ".").strip()))
                except ValueError:
                    logs.append(f"не смогла распознать прогресс для заказа {op.order_id}: {op.value}")
                    continue
                ok = await self.sheets.update_order_fields(op.order_id, progress_percent=percent)
                logs.append(f"обновила прогресс заказа {op.order_id} до {max(0, min(100, percent))}%" if ok else f"не смогла обновить прогресс заказа {op.order_id}")
                continue
            if op.field_name == "story_points":
                try:
                    points = int(float(op.value.replace(",", ".").strip()))
                except ValueError:
                    logs.append(f"не смогла распознать сторипоинты для заказа {op.order_id}: {op.value}")
                    continue
                ok = await self.sheets.update_order_fields(op.order_id, story_points=points)
                logs.append(f"обновила сторипоинты заказа {op.order_id} до {max(0, points)}" if ok else f"не смогла обновить сторипоинты заказа {op.order_id}")
                continue
            if op.field_name == "status":
                status = op.value.strip().lower() or "active"
                ok = await self.sheets.update_order_fields(op.order_id, status=status)
                logs.append(f"обновила статус заказа {op.order_id} на {status}" if ok else f"не смогла обновить статус заказа {op.order_id}")
                continue
            logs.append(f"не поддерживаю поле {op.field_name} для заказа {op.order_id}")
        return logs

    @staticmethod
    def _matches_reference(reference: str, blob: str) -> bool:
        ref = reference.strip().lower()
        if not ref:
            return False
        if ref in blob:
            return True
        tokens = [t for t in re.findall(r"[a-zа-яё0-9]{3,}", ref) if t not in ORDER_QUERY_STOPWORDS]
        for token in tokens:
            if token in blob:
                return True
            # Tiny fuzzy support for russian inflections like Иван/Ивана.
            if len(token) > 4 and token[:-1] in blob:
                return True
        return False

    def _select_relevant_orders(
        self,
        normalized: str,
        orders: list[dict[str, object]],
        from_user_id: int | None = None,
    ) -> list[dict[str, object]]:
        if not orders:
            return []
        if self._is_orders_list_query(normalized):
            return orders
        if from_user_id is not None:
            contextual = self._resolve_contextual_orders(normalized, orders, from_user_id)
            if contextual:
                return contextual

        reference = self._extract_order_reference(normalized)
        if reference:
            ref_lower = reference.lower()
            by_id = [o for o in orders if str(o.get("order_id", "")).lower() == ref_lower]
            if by_id:
                return by_id
            by_title_or_client = [o for o in orders if self._matches_reference(ref_lower, self._order_search_blob(o))]
            if by_title_or_client:
                return by_title_or_client

        id_match = ORDER_ID_PATTERN.search(normalized)
        if id_match:
            target_id = id_match.group("order_id").lower()
            by_id = [o for o in orders if str(o.get("order_id", "")).lower() == target_id]
            if by_id:
                return by_id

        tokens = self._query_tokens(normalized)
        if not tokens:
            return orders
        scored: list[tuple[int, dict[str, object]]] = []
        for order in orders:
            blob = self._order_search_blob(order)
            score = sum(1 for token in tokens if token in blob)
            if score > 0:
                scored.append((score, order))
        if not scored:
            return orders
        scored.sort(key=lambda item: item[0], reverse=True)
        top_score = scored[0][0]
        return [order for score, order in scored if score == top_score]

    async def _generate_order_reply(
        self,
        *,
        query: str,
        all_orders: list[dict[str, object]],
        relevant_orders: list[dict[str, object]],
        from_user_id: int,
        force_list_view: bool = False,
        memory_context: str = "",
    ) -> str:
        self._remember_order_context(from_user_id, relevant_orders if relevant_orders else all_orders)
        if self.llm is None:
            if force_list_view:
                return self._format_orders_digest(all_orders)
            if len(relevant_orders) == 1:
                self._last_order_by_user[from_user_id] = str(relevant_orders[0].get("order_id", "")).lower()
                return self._format_order_details(relevant_orders[0])
            if len(relevant_orders) < len(all_orders):
                return "Похоже, речь вот про эти заказы: " + "; ".join(self._format_order_line(o) for o in relevant_orders)
            return self._format_orders_digest(all_orders)

        all_orders_lines = "\n".join(self._format_order_line(o) for o in all_orders)
        relevant_lines = "\n".join(self._format_order_line(o) for o in relevant_orders) if relevant_orders else "не определены"
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты формируешь живой и фактический ответ по заказам мастерской. "
                    "Отвечай только на основе переданных данных, ничего не выдумывай. "
                    "Пиши разговорно и естественно, без канцелярита и механических маркированных списков, "
                    "если пользователь не просил именно список. "
                    "ID упоминай только при неоднозначности или если пользователь просит ID. "
                    "Если релевантных заказов несколько, коротко перечисли их и попроси уточнить."
                ),
            },
            {"role": "system", "content": self._persona},
            {
                "role": "system",
                "content": self.soul.module_style_prompt("secretary")
                if self.soul is not None
                else "Говори естественно и без выдуманных фактов.",
            },
            {
                "role": "user",
                "content": (
                    f"Запрос пользователя: {query}\n\n"
                    f"Релевантная память:\n{memory_context[:900] or 'нет'}\n\n"
                    f"Все активные заказы:\n{all_orders_lines}\n\n"
                    f"Предварительно релевантные заказы:\n{relevant_lines}"
                ),
            },
        ]
        reply = await self.llm.complete(messages, max_tokens=100000, timeout_seconds=8.0)
        if "внешний AI сейчас недоступен" in reply.lower():
            if force_list_view:
                return self._format_orders_digest(all_orders)
            if len(relevant_orders) == 1:
                self._last_order_by_user[from_user_id] = str(relevant_orders[0].get("order_id", "")).lower()
                return self._format_order_details(relevant_orders[0])
            if len(relevant_orders) < len(all_orders):
                return "Похоже, речь вот про эти заказы: " + "; ".join(self._format_order_line(o) for o in relevant_orders)
            return self._format_orders_digest(all_orders)
        if len(relevant_orders) == 1:
            self._last_order_by_user[from_user_id] = str(relevant_orders[0].get("order_id", "")).lower()
        return reply

    def _is_order_related_message(self, normalized: str, from_user_id: int) -> bool:
        lowered = normalized.lower()
        if self._looks_like_order_query(normalized):
            return True
        if self._looks_like_order_mutation_intent(normalized):
            return True
        if self._looks_like_order_creation_intent(normalized):
            return True
        if ORDER_PATTERN.search(normalized) or PROGRESS_PATTERN.search(normalized):
            return True
        if PROGRESS_FLEX_PATTERN.search(normalized) and from_user_id in self._last_order_by_user:
            return True
        if from_user_id in self._last_order_by_user and MUTATION_FIELD_KEYWORDS_PATTERN.search(lowered):
            return True
        if from_user_id in self._last_order_by_user and any(
            token in lowered for token in ("перв", "втор", "трет", "последн", "этот", "эта", "это", "там")
        ):
            return True
        return "заказ" in lowered

    @staticmethod
    def _is_digest_request(text: str) -> bool:
        normalized = text.strip()
        if not normalized:
            return False
        return bool(DIGEST_REQUEST_PATTERN.search(normalized))

    async def try_handle_command(
        self,
        text: str,
        from_user_id: int,
        memory_context: str = "",
        sender_display_name: str = "",
    ) -> ParseResult:
        normalized = text.strip()
        sender_display_name = (sender_display_name or "").strip()
        if sender_display_name:
            self._user_label_by_id[from_user_id] = sender_display_name

        if from_user_id in self._pending_create_by_user:
            pending_orders = await self.sheets.list_active_orders()
            create_choice_result = await self._resolve_pending_create_choice(
                normalized=normalized,
                from_user_id=from_user_id,
                orders_snapshot=pending_orders,
            )
            if create_choice_result is not None:
                return create_choice_result

        if from_user_id in self._pending_mutation_by_user:
            if self._is_negative_confirmation(normalized):
                self._pending_mutation_by_user.pop(from_user_id, None)
                return ParseResult(True, "Ок, отменяю изменения по заказам.")
            if self._is_positive_confirmation(normalized):
                pending = self._pending_mutation_by_user.pop(from_user_id)
                applied_logs = await self._apply_mutation_operations(
                    pending.operations,
                    from_user_id=from_user_id,
                    sender_display_name=sender_display_name,
                )
                if not applied_logs:
                    return ParseResult(True, "Не смогла применить изменения: не распознала операции.")
                return ParseResult(True, "Приняла подтверждение. " + "; ".join(applied_logs) + ".")

        if self._is_digest_request(normalized):
            if self.digest is None:
                return ParseResult(True, "Функция дайджеста пока не подключена.")
            digest = await self.digest.build_digest(window_hours=24, trigger="manual")
            return ParseResult(True, digest.text)

        orders_snapshot: list[dict[str, object]] = []
        if self._is_order_related_message(normalized, from_user_id):
            orders_snapshot = await self.sheets.list_active_orders()

        order_match = ORDER_PATTERN.search(normalized)
        if order_match:
            title = order_match.group("title").strip()
            amount = float(order_match.group("amount").replace(",", "."))
            due_date = datetime.fromisoformat(order_match.group("date"))
            client = (order_match.group("client") or "unknown").strip()
            if not orders_snapshot:
                orders_snapshot = await self.sheets.list_active_orders()
            return await self._create_or_ask_duplicate_confirmation(
                from_user_id=from_user_id,
                title=title,
                client=client,
                amount=amount,
                due_date=due_date,
                responsible="",
                materials_amount=0.0,
                progress_percent=0,
                story_points=0,
                orders_snapshot=orders_snapshot,
                success_message_template="Записала заказ {order[title]}: дедлайн {due_date_iso}, клиент {client}.",
            )

        progress_match = PROGRESS_PATTERN.search(normalized)
        if progress_match:
            order_id = progress_match.group("order_id").lower()
            percent = max(0, min(100, int(progress_match.group("percent"))))
            status = progress_match.group("status")
            updated = await self.sheets.update_order_progress(order_id, percent, status)
            if updated:
                self._last_order_by_user[from_user_id] = order_id
                return ParseResult(True, f"Обновила прогресс заказа {order_id} до {percent}%.")
            return ParseResult(True, f"Не нашла заказ {order_id} среди активных.")

        is_create_intent = self._looks_like_order_creation_intent(normalized)
        is_mutation_intent = self._looks_like_order_mutation_intent(normalized)
        is_query_intent = self._looks_like_order_query(normalized)
        has_contextual_followup_hint = bool(
            re.search(r"\b(?:перв\w*|втор\w*|трет\w*|последн\w*|этот|эта|это|там|тут)\b", normalized.lower())
            or re.search(r"\d+\s*(?:%|процент\w*|к|k|тыс\.?)\b", normalized.lower())
            or MUTATION_FIELD_KEYWORDS_PATTERN.search(normalized.lower())
        )
        llm_mutation_candidate = bool(
            self.llm is not None
            and orders_snapshot
            and not is_create_intent
            and not is_query_intent
            and from_user_id in self._last_order_by_user
            and has_contextual_followup_hint
        )

        if (is_mutation_intent or llm_mutation_candidate) and not is_create_intent:
            if not orders_snapshot:
                return ParseResult(True, "Активных заказов сейчас нет, менять пока нечего.")
            plan = await self._plan_mutation_with_llm(
                query=normalized,
                orders=orders_snapshot,
                from_user_id=from_user_id,
                memory_context=memory_context,
            )
            operations = self._validate_operations(self._dedupe_operations(plan.operations))
            if not operations:
                return ParseResult(
                    True,
                    "Не поймала точное изменение. Сформулируй чуть конкретнее: что меняем и в каком заказе.",
                )
            if plan.ambiguity and plan.confidence < 0.35:
                return ParseResult(True, plan.preview or f"Нужно уточнение: {plan.ambiguity}")
            safe_ops = [op for op in operations if not self._is_destructive_operation(op)]
            destructive_ops = [op for op in operations if self._is_destructive_operation(op)]
            logs: list[str] = []
            if safe_ops:
                logs.extend(
                    await self._apply_mutation_operations(
                        safe_ops,
                        from_user_id=from_user_id,
                        sender_display_name=sender_display_name,
                    )
                )
                for op in safe_ops:
                    self._last_order_by_user[from_user_id] = op.order_id
            if destructive_ops:
                preview_text = plan.preview or "Нашла потенциально опасное изменение."
                self._pending_mutation_by_user[from_user_id] = PendingMutationPlan(
                    source_text=normalized,
                    preview_text=preview_text,
                    operations=destructive_ops,
                )
                destructive_lines = [
                    (
                        f"{op.action}:{op.field_name} {op.order_id}" + (f" => {op.value}" if op.value else "")
                        if op.action == "set_field"
                        else f"{op.action} {op.order_id}" + (f" => {op.value}" if op.value else "")
                    )
                    for op in destructive_ops
                ]
                prefix = ("Сразу внесла: " + "; ".join(logs) + ". ") if logs else ""
                return ParseResult(
                    True,
                    prefix
                    + f"{preview_text} Для финального шага подтверди `да`/`подтверждаю`: "
                    + "; ".join(destructive_lines),
                )
            if logs:
                return ParseResult(True, "Готово: " + "; ".join(logs) + ".")
            return ParseResult(True, "Операции распознаны, но не получилось применить изменения.")

        if is_query_intent and not is_create_intent:
            if not orders_snapshot:
                return ParseResult(True, "Активных заказов сейчас нет.")
            llm_selection = await self._select_orders_with_llm(
                query=normalized,
                orders=orders_snapshot,
                from_user_id=from_user_id,
                memory_context=memory_context,
            )
            if llm_selection.order_ids:
                id_set = set(llm_selection.order_ids)
                relevant_orders = [
                    order for order in orders_snapshot if str(order.get("order_id", "")).lower() in id_set
                ]
            else:
                relevant_orders = self._select_relevant_orders(normalized, orders_snapshot, from_user_id=from_user_id)
            if llm_selection.clarification and llm_selection.confidence < 0.35 and len(relevant_orders) != 1:
                return ParseResult(True, llm_selection.clarification)
            reply = await self._generate_order_reply(
                query=normalized,
                all_orders=orders_snapshot,
                relevant_orders=relevant_orders,
                from_user_id=from_user_id,
                force_list_view=self._is_orders_list_query(normalized),
                memory_context=memory_context,
            )
            return ParseResult(True, reply)

        progress_flex = PROGRESS_FLEX_PATTERN.search(normalized)
        if progress_flex and from_user_id in self._last_order_by_user:
            order_id = self._last_order_by_user[from_user_id]
            percent = max(0, min(100, int(progress_flex.group("percent"))))
            updated = await self.sheets.update_order_progress(order_id, percent)
            if updated:
                return ParseResult(True, f"Ок, готовность по заказу {order_id} теперь {percent}%.")

        flex_result = await self._try_handle_flexible_order(
            normalized,
            from_user_id,
            orders_snapshot,
            sender_display_name=sender_display_name,
        )
        if flex_result is not None:
            if orders_snapshot:
                self._remember_order_context(from_user_id, orders_snapshot)
            return flex_result

        todo_match = TODO_PATTERN.search(normalized)
        todo_natural_match = TODO_NATURAL_WITH_THAT_PATTERN.search(normalized) or TODO_NATURAL_PATTERN.search(normalized)
        if todo_match or todo_natural_match:
            relative_due_at = self._extract_relative_todo_due_date(normalized)
            if todo_match:
                to_user = int(todo_match.group("to_user"))
                task_text = todo_match.group("text").strip()
                due_raw = todo_match.group("date")
                due_at = self._parse_todo_due_date(due_raw) if due_raw else relative_due_at
            else:
                assert todo_natural_match is not None
                to_user_raw = todo_natural_match.group("to_user")
                resolved_user_id = self._resolve_todo_user_id(to_user_raw, from_user_id=from_user_id)
                if resolved_user_id is None:
                    return ParseResult(
                        True,
                        f"Не поняла, кому передать просьбу «{to_user_raw}». Напиши @username, user<ID> или имя участника.",
                    )
                to_user = resolved_user_id
                task_text = todo_natural_match.group("text").strip()
                due_raw = todo_natural_match.group("date")
                due_at = self._parse_todo_due_date(due_raw) if due_raw else relative_due_at
            todo = await self.sheets.add_todo(
                from_user_id=from_user_id,
                to_user_id=to_user,
                text=task_text,
                due_at=due_at,
            )
            await self._notify_todo_target(todo)
            return ParseResult(True, f"Поручение создано (ID {todo['todo_id']}) для user{to_user}.")

        if normalized.lower() in {"список задач", "/todos"}:
            todos = await self.sheets.list_open_todos()
            if not todos:
                return ParseResult(True, "Открытых поручений нет.")
            rows = [f"- {t['text']} (для user{t['to_user_id']}, ID {t['todo_id']})" for t in todos]
            return ParseResult(True, "Текущие поручения:\n" + "\n".join(rows))

        return ParseResult(False, "")
