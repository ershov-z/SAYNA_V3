from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
import logging
from pathlib import Path
from typing import Any

from bot.config import Settings, get_settings
from mempalace.palace import build_closet_lines, get_closets_collection, get_collection, upsert_closet_lines

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SeedFact:
    title: str
    text: str
    room: str
    user_id: int | None = None


def _entry_id(wing: str, title: str, text: str) -> str:
    stamp = f"{wing}|{title}|{text}"
    return f"{wing}_{sha1(stamp.encode('utf-8')).hexdigest()[:16]}"


def _seed_facts() -> list[SeedFact]:
    return [
        SeedFact(
            title="users_map",
            room="profiles",
            text=(
                "Пользователи мастерской: "
                "Захар (id=752142337, @fenptropill_cosplay), "
                "Катя (id=495538754, @Tenebris_cosplay), "
                "Софа (id=381448542, @salmo_salar)."
            ),
        ),
        SeedFact(
            title="zahar_profile",
            room="profiles",
            user_id=752142337,
            text=(
                "Захар: дата рождения 8 мая 1998. "
                "Профессия QA инженер. "
                "Любит игры Marvel Rivals, Helldivers, Oxygen Not Included. "
                "В мастерской: крафтер, управляет 3D-принтерами, закупками и финансами."
            ),
        ),
        SeedFact(
            title="katya_profile",
            room="profiles",
            user_id=495538754,
            text=(
                "Катя: дата рождения 8 февраля 1999. "
                "Швея, шьет костюмы. "
                "Любит Warcraft и Star Wars."
            ),
        ),
        SeedFact(
            title="sofa_profile",
            room="profiles",
            user_id=381448542,
            text=(
                "Софа: дата рождения 24 августа 2002. "
                "Археолог по образованию. "
                "В мастерской занимается покрасом, шлифовкой и сборкой. "
                "Любимый современный гонщик Формулы 1: Макс Ферстаппен."
            ),
        ),
        SeedFact(
            title="relationship",
            room="relationships",
            text="Захар, Катя и Софа состоят в полиаморной триаде и живут вместе.",
        ),
        SeedFact(
            title="pet",
            room="household",
            text="У них есть кошка по имени Юта.",
        ),
    ]

def _result_get(result: Any, key: str) -> list[Any]:
    getter = getattr(result, "get", None)
    if callable(getter):
        value = getter(key, [])
        return value or []
    return []


def _collection_has_entries(collection: Any) -> bool:
    counter = getattr(collection, "count", None)
    if callable(counter):
        try:
            return int(counter()) > 0
        except Exception:
            logger.warning("Seed precheck via count() failed; fallback to get()", exc_info=True)
    getter = getattr(collection, "get", None)
    if callable(getter):
        try:
            result = getter(limit=1)
            ids = _result_get(result, "ids")
            if ids and isinstance(ids[0], list):
                return bool(ids[0])
            return bool(ids)
        except Exception:
            logger.warning("Seed precheck via get() failed; treating as empty", exc_info=True)
    return False


def _build_rows(settings: Settings, now: str) -> list[dict[str, Any]]:
    shared_wing = f"{settings.mempalace_wing_prefix}_shared"
    rows: list[dict[str, Any]] = []
    for fact in _seed_facts():
        target_wings = [shared_wing]
        if fact.user_id is not None:
            target_wings.append(f"{settings.mempalace_wing_prefix}_user_{fact.user_id}")

        for wing in target_wings:
            entry_id = _entry_id(wing, fact.title, fact.text)
            rows.append(
                {
                    "id": entry_id,
                    "document": f"SYSTEM: {fact.text}",
                    "metadata": {
                        "wing": wing,
                        "room": fact.room,
                        "chat_id": "seed",
                        "user_id": str(fact.user_id or 0),
                        "role": "system",
                        "source_file": "seed/manual_profiles",
                        "timestamp": now,
                        "filed_at": now,
                        "ingest_mode": "manual_seed",
                    },
                }
            )
    return rows


def seed_if_needed(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    if not settings.mempalace_enabled:
        logger.info("Memory seed skipped: MEMPALACE_ENABLED=false")
        return 0

    palace_path = Path(settings.mempalace_palace_dir)
    palace_path.mkdir(parents=True, exist_ok=True)
    collection = get_collection(str(palace_path), create=True)
    if _collection_has_entries(collection):
        logger.info("Memory seed skipped: palace already contains entries")
        return 0

    closets = get_closets_collection(str(palace_path), create=True)
    now = datetime.now(timezone.utc).isoformat()
    rows = _build_rows(settings, now)
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
            closets_col=closets,
            closet_id_base=f"seedcloset_{row['id']}",
            lines=closet_lines,
            metadata=row["metadata"],
        )

    logger.info("Seeded %s memory entries into %s", len(rows), palace_path)
    return len(rows)


def main() -> None:
    seeded = seed_if_needed()
    if seeded:
        settings = get_settings()
        print(f"Seeded {seeded} memory entries into {settings.mempalace_palace_dir}")
    else:
        print("Seed skipped: palace already initialized or memory disabled")


if __name__ == "__main__":
    main()
