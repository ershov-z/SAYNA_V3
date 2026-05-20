from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path

from bot.config import get_settings
from mempalace.palace import build_closet_lines, get_closets_collection, get_collection, upsert_closet_lines


@dataclass(slots=True)
class SeedFact:
    title: str
    text: str
    room: str
    user_id: int | None = None


def _entry_id(wing: str, title: str, text: str) -> str:
    stamp = f"{wing}|{title}|{text}"
    return f"{wing}_{sha1(stamp.encode('utf-8')).hexdigest()[:16]}"


def main() -> None:
    settings = get_settings()
    palace_path = Path(settings.mempalace_palace_dir)
    palace_path.mkdir(parents=True, exist_ok=True)

    shared_wing = f"{settings.mempalace_wing_prefix}_shared"

    users = {
        752142337: {"name": "Захар", "username": "fenptropill_cosplay"},
        495538754: {"name": "Катя", "username": "Tenebris_cosplay"},
        381448542: {"name": "Софа", "username": "salmo_salar"},
    }

    facts: list[SeedFact] = [
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

    collection = get_collection(str(palace_path), create=True)
    closets = get_closets_collection(str(palace_path), create=True)
    now = datetime.now(timezone.utc).isoformat()

    rows = []
    for fact in facts:
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

    print(f"Seeded {len(rows)} memory entries into {palace_path}")


if __name__ == "__main__":
    main()
