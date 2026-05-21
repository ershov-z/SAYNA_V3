from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bot.config import Settings

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bot.services.chad_ai import ChadAIClient

ORDER_ROOM_PATTERNS = (
    r"\bзаказ\w*",
    r"\bдедлайн\w*",
    r"\bсрок\w*",
    r"\bсдать\b",
    r"\bпрогресс\w*",
    r"\bготовн\w*",
    r"\bклиент\w*",
)

TASK_ROOM_PATTERNS = (
    r"\bзадач\w*",
    r"\bпоруч\w*",
    r"\bдолжен\b",
    r"\bдолжна\b",
    r"\bнапомни\w*",
    r"\bсдела\w*",
)

MATERIAL_ROOM_PATTERNS = (
    r"\bматериал\w*",
    r"\bфурнитур\w*",
    r"\bткан\w*",
    r"\bкупи\w*",
    r"\bмолни\w*",
)

PROFILE_QUERY_PATTERNS = (
    r"\bкто\b",
    r"\bо\s+соф\w*",
    r"\bо\s+кат\w*",
    r"\bо\s+захар\w*",
    r"\bобо\s+мне\b",
    r"\bпро\s+меня\b",
    r"\bчто\s+ты\s+знаешь\b",
    r"\bфакт\w*\b",
    r"\bлюбим\w+\s+гонщик\w*",
)


@dataclass(slots=True)
class MemoryMessage:
    role: str
    user_id: int
    chat_id: int
    text: str
    created_at: str


@dataclass(slots=True)
class MemoryCandidate:
    timestamp: str
    text: str


class MemPalaceService:
    """
    Direct in-process MemPalace integration.

    Stores dialogue directly in local palace collection (no CLI subprocess).
    """

    def __init__(self, settings: Settings, reranker: "ChadAIClient | None" = None) -> None:
        self.settings = settings
        self.reranker = reranker
        self.palace_path = Path(settings.mempalace_palace_dir)
        self.palace_path.mkdir(parents=True, exist_ok=True)
        self._collection: Any | None = None
        self._closets_collection: Any | None = None

    def _user_wing(self, user_id: int) -> str:
        return f"{self.settings.mempalace_wing_prefix}_user_{user_id}"

    def _shared_wing(self) -> str:
        return f"{self.settings.mempalace_wing_prefix}_shared"

    @staticmethod
    def _count_pattern_hits(text: str, patterns: tuple[str, ...]) -> int:
        return sum(1 for pattern in patterns if re.search(pattern, text, flags=re.IGNORECASE))

    def _infer_room(self, text: str, chat_id: int) -> str:
        lowered = text.lower()
        order_hits = self._count_pattern_hits(lowered, ORDER_ROOM_PATTERNS)
        task_hits = self._count_pattern_hits(lowered, TASK_ROOM_PATTERNS)
        material_hits = self._count_pattern_hits(lowered, MATERIAL_ROOM_PATTERNS)

        # Keep workshop memory clean: classify as work room only with strong signal.
        if order_hits >= 2:
            return "orders"
        if task_hits >= 2:
            return "tasks"
        if material_hits >= 1 and (order_hits >= 1 or task_hits >= 1):
            return "materials"
        if chat_id < 0:
            return "chatter_group"
        return "chatter_private"

    def _search_rooms_for_query(self, query: str, chat_id: int | None = None) -> list[str]:
        lowered = query.lower()
        order_hits = self._count_pattern_hits(lowered, ORDER_ROOM_PATTERNS)
        task_hits = self._count_pattern_hits(lowered, TASK_ROOM_PATTERNS)
        material_hits = self._count_pattern_hits(lowered, MATERIAL_ROOM_PATTERNS)
        profile_hits = self._count_pattern_hits(lowered, PROFILE_QUERY_PATTERNS)

        preferred: list[str] = []
        if profile_hits:
            preferred.extend(["profiles", "relationships", "household"])
        if order_hits:
            preferred.append("orders")
        if task_hits:
            preferred.append("tasks")
        if material_hits:
            preferred.append("materials")

        for room in ("profiles", "relationships", "household", "orders", "tasks", "materials"):
            if room not in preferred:
                preferred.append(room)

        # Chatter is fallback, but still searchable.
        if chat_id is not None and chat_id < 0:
            preferred.append("chatter_group")
        preferred.append("chatter_private")
        preferred.append("chatter_group")
        return list(dict.fromkeys(preferred))

    @staticmethod
    def _extract_role_text(document: str) -> str:
        text = document.strip()
        if text.upper().startswith("USER:"):
            return text[5:].strip()
        if text.upper().startswith("ASSISTANT:"):
            return text[10:].strip()
        if text.upper().startswith("SYSTEM:"):
            return text[7:].strip()
        return text

    def _entry_id(self, wing: str, user_id: int, chat_id: int, role: str, text: str, created_at: str) -> str:
        fingerprint = sha1(f"{chat_id}|{user_id}|{role}|{created_at}|{text}".encode("utf-8")).hexdigest()[:16]
        return f"{wing}_{fingerprint}"

    @staticmethod
    def _result_get(result: Any, key: str) -> list[Any]:
        getter = getattr(result, "get", None)
        if callable(getter):
            value = getter(key, [])
            return value or []
        return []

    def _get_collection(self) -> Any:
        if self._collection is not None:
            return self._collection
        try:
            from mempalace.palace import get_collection
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("MemPalace package is not installed. Install `mempalace` to run the bot.") from exc

        self._collection = get_collection(str(self.palace_path), create=True)
        return self._collection

    def _get_closets_collection(self) -> Any:
        if self._closets_collection is not None:
            return self._closets_collection
        try:
            from mempalace.palace import get_closets_collection
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("MemPalace package is not installed. Install `mempalace` to run the bot.") from exc
        self._closets_collection = get_closets_collection(str(self.palace_path), create=True)
        return self._closets_collection

    async def remember(self, role: str, user_id: int, chat_id: int, text: str) -> None:
        if not self.settings.mempalace_enabled:
            return
        cleaned = text.strip()
        if not cleaned:
            return

        payload = MemoryMessage(
            role=role,
            user_id=user_id,
            chat_id=chat_id,
            text=cleaned,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        room = self._infer_room(cleaned, chat_id)
        logger.info(
            "memory_remember_start chat_id=%s user_id=%s role=%s room=%s chars=%s",
            chat_id,
            user_id,
            role,
            room,
            len(cleaned),
        )
        def _upsert_in_collection() -> None:
            from mempalace.palace import build_closet_lines, upsert_closet_lines
            collection = self._get_collection()
            closets_collection = self._get_closets_collection()

            rows = []
            for wing in (self._shared_wing(), self._user_wing(user_id)):
                entry_id = self._entry_id(wing, user_id, chat_id, role, cleaned, payload.created_at)
                rows.append(
                    {
                        "id": entry_id,
                        "document": f"{role.upper()}: {cleaned}",
                        "metadata": {
                            "wing": wing,
                            "room": room,
                            "chat_id": str(chat_id),
                            "user_id": str(user_id),
                            "role": role,
                            "source_file": f"telegram/chat_{chat_id}",
                            "timestamp": payload.created_at,
                            "filed_at": payload.created_at,
                            "ingest_mode": "direct_runtime",
                        },
                    }
                )
            collection.upsert(
                ids=[row["id"] for row in rows],
                documents=[row["document"] for row in rows],
                metadatas=[row["metadata"] for row in rows],
            )
            for row in rows:
                closet_lines = build_closet_lines(
                    source_file=row["metadata"]["source_file"],
                    drawer_ids=[row["id"]],
                    content=row["document"],
                    wing=row["metadata"]["wing"],
                    room=row["metadata"]["room"],
                )
                upsert_closet_lines(
                    closets_col=closets_collection,
                    closet_id_base=f"rtcloset_{row['id']}",
                    lines=closet_lines,
                    metadata=row["metadata"],
                )

        _upsert_in_collection()
        logger.info(
            "memory_remember_done chat_id=%s user_id=%s role=%s room=%s",
            chat_id,
            user_id,
            role,
            room,
        )

    async def search_context(
        self,
        query: str,
        user_id: int | None = None,
        limit: int = 3,
        chat_id: int | None = None,
        fallback_user_ids: list[int] | None = None,
    ) -> str:
        if not self.settings.mempalace_enabled:
            return ""
        logger.info(
            "memory_search_start chat_id=%s user_id=%s fallback_user_ids=%s limit=%s query=%r",
            chat_id,
            user_id,
            fallback_user_ids or [],
            limit,
            query[:220],
        )
        final_limit = max(1, min(limit, self.settings.memory_rerank_final_limit))
        candidate_limit = max(final_limit, self.settings.memory_rerank_candidate_limit)
        min_candidates = max(2, self.settings.memory_rerank_min_candidates)

        def _scoped_search(wing: str, room: str | None) -> list[MemoryCandidate]:
            collection = self._get_collection()
            where: dict[str, Any] = {"wing": wing}
            if room is not None:
                where = {"$and": [{"wing": wing}, {"room": room}]}
            try:
                result = collection.query(
                    query_texts=[query],
                    n_results=max(candidate_limit, 6),
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )
            except Exception as exc:
                if room is not None:
                    logger.warning(
                        "mempalace scoped query failed for wing=%s room=%s, fallback to wing-only: %s",
                        wing,
                        room,
                        exc,
                    )
                    try:
                        result = collection.query(
                            query_texts=[query],
                            n_results=max(candidate_limit, 6),
                            where={"wing": wing},
                            include=["documents", "metadatas", "distances"],
                        )
                    except Exception as fallback_exc:
                        logger.warning(
                            "mempalace scoped fallback query failed for wing=%s room=%s: %s",
                            wing,
                            room,
                            fallback_exc,
                        )
                        return []
                else:
                    logger.warning("mempalace scoped query failed for wing=%s room=%s: %s", wing, room, exc)
                    return []

            docs = self._result_get(result, "documents")
            metas = self._result_get(result, "metadatas")
            if not docs:
                return []
            first_docs = docs[0] if docs and isinstance(docs[0], list) else docs
            first_metas = metas[0] if metas and isinstance(metas[0], list) else metas

            paired: list[MemoryCandidate] = []
            for doc, meta in zip(first_docs or [], first_metas or []):
                meta = meta or {}
                # hard guard against cross-wing leakage
                if str(meta.get("wing", "")) != wing:
                    continue
                if room is not None and str(meta.get("room", "")) != room:
                    continue
                text = self._extract_role_text(str(doc or ""))
                ts = str(meta.get("timestamp") or meta.get("filed_at") or "")
                if text:
                    paired.append(MemoryCandidate(timestamp=ts, text=text[:700]))
            if not paired:
                return []
            return paired

        async def _build_context_from_candidates(candidates: list[MemoryCandidate]) -> str:
            if not candidates:
                return ""
            selected: list[MemoryCandidate] = candidates
            reranked = False
            if (
                self.settings.memory_rerank_enabled
                and self.reranker is not None
                and len(candidates) >= min_candidates
            ):
                try:
                    indices = await self.reranker.rerank_memory_candidates(
                        query=query,
                        candidates=[item.text for item in candidates[:candidate_limit]],
                        timeout_seconds=self.settings.memory_rerank_timeout_seconds,
                        top_k=final_limit,
                    )
                except Exception as exc:
                    logger.warning("memory rerank failed, fallback to base order: %s", exc)
                    indices = []
                if indices:
                    scoped = candidates[:candidate_limit]
                    selected = [scoped[idx] for idx in indices if 0 <= idx < len(scoped)]
                    reranked = bool(selected)
            if reranked:
                logger.info(
                    "memory_search_reranked candidates=%s final_limit=%s",
                    len(candidates),
                    final_limit,
                )
                return "\n".join(item.text for item in selected[:final_limit])
            # keep chronological coherence in fallback context.
            selected = sorted(selected, key=lambda item: item.timestamp)
            return "\n".join(item.text for item in selected[-final_limit:])

        rooms = self._search_rooms_for_query(query, chat_id=chat_id)
        # 1) Shared first (single conversation context for the whole chat).
        for room in rooms:
            shared_hits = _scoped_search(self._shared_wing(), room)
            if shared_hits:
                logger.info("memory_search_hit scope=shared room=%s candidates=%s", room, len(shared_hits))
                return await _build_context_from_candidates(shared_hits)
        shared_hits_any = _scoped_search(self._shared_wing(), None)
        if shared_hits_any:
            logger.info("memory_search_hit scope=shared room=any candidates=%s", len(shared_hits_any))
            return await _build_context_from_candidates(shared_hits_any)

        # 2) Personal wings fallback (current user and/or referenced users).
        candidate_user_ids: list[int] = []
        if fallback_user_ids:
            candidate_user_ids.extend(fallback_user_ids)
        if user_id is not None:
            candidate_user_ids.append(user_id)
        # Preserve order, remove duplicates.
        candidate_user_ids = list(dict.fromkeys(candidate_user_ids))

        for candidate_id in candidate_user_ids:
            wing = self._user_wing(candidate_id)
            for room in rooms:
                user_hits = _scoped_search(wing, room)
                if user_hits:
                    logger.info(
                        "memory_search_hit scope=personal wing=%s room=%s candidates=%s",
                        wing,
                        room,
                        len(user_hits),
                    )
                    return await _build_context_from_candidates(user_hits)
            user_hits_any = _scoped_search(wing, None)
            if user_hits_any:
                logger.info(
                    "memory_search_hit scope=personal wing=%s room=any candidates=%s",
                    wing,
                    len(user_hits_any),
                )
                return await _build_context_from_candidates(user_hits_any)

        logger.info("memory_search_miss chat_id=%s user_id=%s", chat_id, user_id)
        return ""

    async def get_recent_chat_messages(self, chat_id: int, limit: int = 15) -> list[dict[str, str]]:
        """
        Return the latest messages for this chat from shared wing.

        This is short-term conversation memory for dialogue continuity.
        """
        if not self.settings.mempalace_enabled:
            return []

        def _fetch() -> list[dict[str, str]]:
            collection = self._get_collection()
            try:
                result = collection.get(
                    where={"wing": self._shared_wing()},
                    include=["documents", "metadatas"],
                )
            except Exception as exc:
                logger.warning("recent chat fetch failed for chat_id=%s: %s", chat_id, exc)
                return []

            docs = self._result_get(result, "documents")
            metas = self._result_get(result, "metadatas")
            rows: list[tuple[str, str, str, str]] = []

            for doc, meta in zip(docs or [], metas or []):
                meta = meta or {}
                if str(meta.get("chat_id", "")) != str(chat_id):
                    continue
                role = str(meta.get("role", "")).lower()
                text = str(doc or "").strip()
                ts = str(meta.get("timestamp") or meta.get("filed_at") or "")
                author_user_id = str(meta.get("user_id", "")).strip()
                if not text:
                    continue
                if role not in {"user", "assistant"}:
                    if text.upper().startswith("USER:"):
                        role = "user"
                        text = text[5:].strip()
                    elif text.upper().startswith("ASSISTANT:"):
                        role = "assistant"
                        text = text[10:].strip()
                    else:
                        role = "user"
                rows.append((ts, role, author_user_id, text[:500]))

            rows.sort(key=lambda item: item[0])
            tail = rows[-limit:]
            result: list[dict[str, str]] = []
            for _, role, author_user_id, content in tail:
                if role == "user":
                    label = f"user_{author_user_id}" if author_user_id else "user"
                else:
                    label = "saina"
                result.append({"role": role, "content": f"[{label}] {content}"})
            return result

        rows = _fetch()
        logger.info("memory_recent_chat chat_id=%s limit=%s returned=%s", chat_id, limit, len(rows))
        return rows

    async def get_user_profile_context(self, user_id: int) -> str:
        """Return deterministic profile facts for the current user."""
        if not self.settings.mempalace_enabled:
            return ""

        def _fetch() -> str:
            collection = self._get_collection()
            try:
                result = collection.get(
                    where={"wing": self._user_wing(user_id)},
                    include=["documents", "metadatas"],
                )
            except Exception as exc:
                logger.warning("user profile fetch failed for user_id=%s: %s", user_id, exc)
                return ""
            docs = self._result_get(result, "documents")
            metas = self._result_get(result, "metadatas")
            rows: list[tuple[str, str]] = []
            for doc, meta in zip(docs or [], metas or []):
                meta = meta or {}
                if str(meta.get("wing", "")) != self._user_wing(user_id):
                    continue
                if str(meta.get("room", "")) != "profiles":
                    continue
                text = self._extract_role_text(str(doc or ""))
                ts = str(meta.get("timestamp") or meta.get("filed_at") or "")
                if text:
                    rows.append((ts, text))
            rows.sort(key=lambda item: item[0])
            return "\n".join(text for _, text in rows[-3:])

        profile = _fetch()
        logger.info("memory_profile_fetch user_id=%s chars=%s", user_id, len(profile))
        return profile

    async def list_shared_messages_window(
        self,
        *,
        since: datetime,
        until: datetime | None = None,
        chat_id: int | None = None,
        limit: int = 400,
    ) -> list[MemoryMessage]:
        """
        Return shared-wing messages in the requested UTC time window.
        """
        if not self.settings.mempalace_enabled:
            return []

        start_utc = since.astimezone(timezone.utc) if since.tzinfo else since.replace(tzinfo=timezone.utc)
        end_utc: datetime | None = None
        if until is not None:
            end_utc = until.astimezone(timezone.utc) if until.tzinfo else until.replace(tzinfo=timezone.utc)

        def _parse_iso_utc(raw: object) -> datetime | None:
            text = str(raw or "").strip()
            if not text:
                return None
            text = text.replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return None
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

        def _fetch() -> list[MemoryMessage]:
            collection = self._get_collection()
            try:
                result = collection.get(
                    where={"wing": self._shared_wing()},
                    include=["documents", "metadatas"],
                )
            except Exception as exc:
                logger.warning("shared messages fetch failed for digest window: %s", exc)
                return []

            docs = self._result_get(result, "documents")
            metas = self._result_get(result, "metadatas")
            rows: list[tuple[datetime, MemoryMessage]] = []
            for doc, meta in zip(docs or [], metas or []):
                meta = meta or {}
                if str(meta.get("wing", "")) != self._shared_wing():
                    continue
                ts_raw = meta.get("timestamp") or meta.get("filed_at") or ""
                ts = _parse_iso_utc(ts_raw)
                if ts is None or ts < start_utc:
                    continue
                if end_utc is not None and ts >= end_utc:
                    continue
                if chat_id is not None and str(meta.get("chat_id", "")) != str(chat_id):
                    continue
                role = str(meta.get("role", "")).lower() or "user"
                if role not in {"user", "assistant"}:
                    continue
                text = self._extract_role_text(str(doc or "")).strip()
                if not text:
                    continue
                user_id_raw = str(meta.get("user_id", "0")).strip()
                chat_id_raw = str(meta.get("chat_id", "0")).strip()
                if not user_id_raw.lstrip("-").isdigit() or not chat_id_raw.lstrip("-").isdigit():
                    continue
                rows.append(
                    (
                        ts,
                        MemoryMessage(
                            role=role,
                            user_id=int(user_id_raw),
                            chat_id=int(chat_id_raw),
                            text=text,
                            created_at=ts.isoformat(),
                        ),
                    )
                )
            rows.sort(key=lambda item: item[0])
            capped = [item for _, item in rows[-max(1, limit) :]]
            return capped

        messages = _fetch()
        logger.info(
            "memory_window_messages since=%s until=%s chat_id=%s returned=%s",
            start_utc.isoformat(),
            end_utc.isoformat() if end_utc else "",
            chat_id,
            len(messages),
        )
        return messages

    async def sweep(self) -> bool:
        """
        Runtime mode writes directly into the palace, so periodic sweep is a no-op.
        Kept for scheduler compatibility and future maintenance hooks.
        """
        return self.settings.mempalace_enabled
