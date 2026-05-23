from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.config import Settings
from bot.scheduler.jobs import build_scheduler
from bot.services.digest import DigestService
from bot.services.memory import MemoryMessage
from bot.services.soul import SoulService


def make_settings(**overrides) -> Settings:
    payload = {
        "TELEGRAM_BOT_TOKEN": "token",
        "CHAD_AI_API_KEY": "key",
        "GOOGLE_SHEET_ID": "sheet",
        "DIGEST_CHAT_ID": -1001234567890,
        "DAILY_DIGEST_HOUR": 23,
        "DAILY_DIGEST_MINUTE": 0,
    }
    payload.update(overrides)
    return Settings(**payload)


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    async def complete(self, messages, max_tokens=100000, *, model=None, timeout_seconds=None, images=None):  # noqa: ANN001
        if not self.responses:
            return "no-response"
        return self.responses.pop(0)


class FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent_messages.append((chat_id, text))


class FakeSheets:
    def __init__(self) -> None:
        self.orders = [
            {
                "order_id": "ab12cd34",
                "title": "Шлем",
                "client": "Клиент",
                "responsible": "Катя",
                "due_date": datetime(2026, 6, 12, tzinfo=timezone.utc).isoformat(),
                "progress_percent": 65,
                "amount": 12000,
            }
        ]

    async def list_active_orders(self) -> list[dict[str, object]]:
        return list(self.orders)


class FakeMemory:
    def __init__(self, *, messages: list[MemoryMessage], existing_facts: set[str] | None = None) -> None:
        self.messages = messages
        self.existing_facts = {self._norm(item) for item in (existing_facts or set())}
        self.saved: list[tuple[str, int, int, str]] = []

    @staticmethod
    def _norm(value: str) -> str:
        return " ".join(value.lower().split())

    async def list_shared_messages_window(self, *, since, until=None, chat_id=None, limit=400):  # noqa: ANN001
        return list(self.messages)

    async def search_context(self, query, user_id=None, limit=3, chat_id=None, fallback_user_ids=None):  # noqa: ANN001
        if self._norm(query) in self.existing_facts:
            return query
        return ""

    async def remember(self, role: str, user_id: int, chat_id: int, text: str) -> None:
        self.saved.append((role, user_id, chat_id, text))
        self.existing_facts.add(self._norm(text.replace("[digest_sync]", "").strip()))


class DummyReminders:
    async def send_order_progress_ping(self) -> None: ...

    async def send_deadline_alerts(self) -> None: ...

    async def send_todo_reminders(self) -> None: ...


class DummyMemory:
    async def sweep(self) -> bool:
        return True


class DummyDigest:
    async def send_daily_digest(self) -> None: ...


@pytest.mark.asyncio
async def test_build_digest_adds_only_missing_facts_to_memory() -> None:
    settings = make_settings()
    soul = SoulService(settings)
    llm = FakeLLM(
        [
            "Итог дня: по заказу Шлем прогресс 65%, клиент подтвердил правки.",
            '{"facts":["По заказу Шлем прогресс 65%","Клиент подтвердил правки"]}',
        ]
    )
    memory = FakeMemory(
        messages=[
            MemoryMessage(
                role="user",
                user_id=1,
                chat_id=-1001,
                text="По шлему уже 65% готовности",
                created_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            )
        ],
        existing_facts={"По заказу Шлем прогресс 65%"},
    )
    digest = DigestService(
        settings=settings,
        bot=FakeBot(),
        llm=llm,
        soul=soul,
        memory=memory,  # type: ignore[arg-type]
        sheets=FakeSheets(),  # type: ignore[arg-type]
    )

    result = await digest.build_digest(window_hours=24, trigger="manual")

    assert "Блок по мастерской" in result.text
    assert "Блок по чату" in result.text
    assert "Итог дня" in result.text
    assert result.facts_total == 2
    assert result.facts_added == 1
    assert len(memory.saved) == 1
    assert "[digest_sync]" in memory.saved[0][3]


@pytest.mark.asyncio
async def test_send_daily_digest_posts_to_digest_chat() -> None:
    settings = make_settings()
    soul = SoulService(settings)
    llm = FakeLLM(
        [
            "Короткий дайджест: движ есть, заказы двигаются.",
            '{"facts":["Заказы двигаются"]}',
        ]
    )
    bot = FakeBot()
    memory = FakeMemory(messages=[], existing_facts=set())
    digest = DigestService(
        settings=settings,
        bot=bot,
        llm=llm,
        soul=soul,
        memory=memory,  # type: ignore[arg-type]
        sheets=FakeSheets(),  # type: ignore[arg-type]
    )

    await digest.send_daily_digest()

    assert bot.sent_messages
    assert bot.sent_messages[0][0] == settings.digest_chat_id
    assert "Блок по мастерской" in bot.sent_messages[0][1]
    assert "Блок по чату" in bot.sent_messages[0][1]


def test_scheduler_registers_daily_digest_job() -> None:
    settings = make_settings()
    scheduler = build_scheduler(settings, DummyReminders(), DummyMemory(), DummyDigest())  # type: ignore[arg-type]
    job = scheduler.get_job("daily_digest")
    assert job is not None
    if scheduler.running:
        scheduler.shutdown(wait=False)


def test_digest_window_uses_23_to_23_boundaries() -> None:
    settings = make_settings(TZ="Europe/Moscow", DAILY_DIGEST_HOUR=23, DAILY_DIGEST_MINUTE=0)
    digest = DigestService(
        settings=settings,
        bot=FakeBot(),  # type: ignore[arg-type]
        llm=FakeLLM([]),  # type: ignore[arg-type]
        soul=SoulService(settings),
        memory=FakeMemory(messages=[]),  # type: ignore[arg-type]
        sheets=FakeSheets(),  # type: ignore[arg-type]
    )
    # 23:30 local time in Moscow (UTC+3) -> same-day 23:00 is window end.
    now_utc = datetime(2026, 5, 21, 20, 30, tzinfo=timezone.utc)
    start, end = digest._window_bounds(24, trigger="scheduled", now=now_utc)  # noqa: SLF001

    assert end == datetime(2026, 5, 21, 20, 0, tzinfo=timezone.utc)
    assert start == datetime(2026, 5, 20, 20, 0, tzinfo=timezone.utc)


def test_manual_digest_window_uses_last_23_to_now() -> None:
    settings = make_settings(TZ="Europe/Moscow", DAILY_DIGEST_HOUR=23, DAILY_DIGEST_MINUTE=0)
    digest = DigestService(
        settings=settings,
        bot=FakeBot(),  # type: ignore[arg-type]
        llm=FakeLLM([]),  # type: ignore[arg-type]
        soul=SoulService(settings),
        memory=FakeMemory(messages=[]),  # type: ignore[arg-type]
        sheets=FakeSheets(),  # type: ignore[arg-type]
    )
    # 17:30 local time in Moscow (UTC+3) -> start at previous-day 23:00 local.
    now_utc = datetime(2026, 5, 21, 14, 30, tzinfo=timezone.utc)
    start, end = digest._window_bounds(24, trigger="manual", now=now_utc)  # noqa: SLF001

    assert start == datetime(2026, 5, 20, 20, 0, tzinfo=timezone.utc)
    assert end == now_utc
