from __future__ import annotations

import pytest

from bot.config import Settings
from bot.services.memory import MemPalaceService


class FakeCollection:
    def __init__(self, scoped_rows: dict[tuple[str, str | None], list[dict[str, str]]]) -> None:
        self._scoped_rows = scoped_rows

    def query(self, query_texts, n_results, where, include):  # noqa: ANN001, D401
        wing = ""
        room = None
        if "$and" in where:
            for part in where["$and"]:
                if "wing" in part:
                    wing = str(part["wing"])
                if "room" in part:
                    room = str(part["room"])
        else:
            wing = str(where.get("wing", ""))
        rows = self._scoped_rows.get((wing, room), [])
        rows = rows[:n_results]
        return {
            "documents": [[row["document"] for row in rows]],
            "metadatas": [[row["meta"] for row in rows]],
        }


class FakeReranker:
    def __init__(self, order: list[int] | None = None, should_raise: bool = False) -> None:
        self.order = order or []
        self.should_raise = should_raise

    async def rerank_memory_candidates(self, *, query, candidates, timeout_seconds, top_k):  # noqa: ANN001
        if self.should_raise:
            raise RuntimeError("rerank failed")
        return self.order[:top_k]


def make_settings(**overrides) -> Settings:
    payload = {
        "TELEGRAM_BOT_TOKEN": "token",
        "CHAD_AI_API_KEY": "key",
        "GOOGLE_SHEET_ID": "sheet",
        "MEMORY_RERANK_ENABLED": True,
        "MEMORY_RERANK_MIN_CANDIDATES": 2,
        "MEMORY_RERANK_CANDIDATE_LIMIT": 8,
        "MEMORY_RERANK_FINAL_LIMIT": 3,
    }
    payload.update(overrides)
    return Settings(**payload)


@pytest.mark.asyncio
async def test_search_context_uses_reranked_order() -> None:
    settings = make_settings()
    memory = MemPalaceService(settings, reranker=FakeReranker(order=[2, 0, 1]))
    shared_wing = f"{settings.mempalace_wing_prefix}_shared"
    memory._collection = FakeCollection(
        {
            (shared_wing, "profiles"): [
                {
                    "document": "USER: Первый факт",
                    "meta": {"wing": shared_wing, "room": "profiles", "timestamp": "2026-05-19T10:00:00Z"},
                },
                {
                    "document": "USER: Второй факт",
                    "meta": {"wing": shared_wing, "room": "profiles", "timestamp": "2026-05-19T10:01:00Z"},
                },
                {
                    "document": "USER: Третий факт",
                    "meta": {"wing": shared_wing, "room": "profiles", "timestamp": "2026-05-19T10:02:00Z"},
                },
            ]
        }
    )
    context = await memory.search_context("что ты знаешь о софе", user_id=752142337, chat_id=-1001, limit=2)
    assert context.splitlines() == ["Третий факт", "Первый факт"]


@pytest.mark.asyncio
async def test_search_context_falls_back_when_rerank_fails() -> None:
    settings = make_settings()
    memory = MemPalaceService(settings, reranker=FakeReranker(should_raise=True))
    shared_wing = f"{settings.mempalace_wing_prefix}_shared"
    memory._collection = FakeCollection(
        {
            (shared_wing, "profiles"): [
                {
                    "document": "USER: Старый факт",
                    "meta": {"wing": shared_wing, "room": "profiles", "timestamp": "2026-05-19T10:00:00Z"},
                },
                {
                    "document": "USER: Новый факт",
                    "meta": {"wing": shared_wing, "room": "profiles", "timestamp": "2026-05-19T10:05:00Z"},
                },
            ]
        }
    )
    context = await memory.search_context("что ты знаешь о софе", user_id=752142337, chat_id=-1001, limit=2)
    assert context.splitlines() == ["Старый факт", "Новый факт"]


@pytest.mark.asyncio
async def test_search_context_uses_personal_when_shared_missing() -> None:
    settings = make_settings()
    target_user_id = 381448542
    personal_wing = f"{settings.mempalace_wing_prefix}_user_{target_user_id}"
    memory = MemPalaceService(settings, reranker=FakeReranker(order=[0]))
    memory._collection = FakeCollection(
        {
            (personal_wing, "profiles"): [
                {
                    "document": "USER: Софа любит Макса Ферстаппена",
                    "meta": {"wing": personal_wing, "room": "profiles", "timestamp": "2026-05-19T11:00:00Z"},
                }
            ]
        }
    )
    context = await memory.search_context(
        "расскажи факт о софе",
        user_id=None,
        chat_id=-1001,
        fallback_user_ids=[target_user_id],
        limit=2,
    )
    assert "Софа любит Макса Ферстаппена" in context
