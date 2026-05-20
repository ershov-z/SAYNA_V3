from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from bot.config import Settings
from bot.services.chad_ai import ChadAIClient
from bot.services.dialogue import DialogueService

logger = logging.getLogger(__name__)


GROUND_TRUTH = """
1) Юзеры:
- Захар: @fenptropill_cosplay
- Катя: @Tenebris_cosplay, id=495538754
- Софа: @salmo_salar, id=381448542

2) Захар:
- дата рождения: 8 мая 1998
- профессия: QA инженер
- игры: Marvel Rivals, Helldivers, Oxygen Not Included
- роль в мастерской: крафтер, управляет 3D-принтерами, закупает, работает с финансами

3) Катя:
- дата рождения: 8 февраля 1999
- швея, шьет костюмы
- любит Warcraft и Star Wars

4) Софа:
- дата рождения: 24 августа 2002
- археолог по образованию
- в мастерской занимается покрасом, шлифовкой, сборкой

5) Отношения:
- Захар, Катя и Софа состоят в полиаморной триаде и живут вместе

6) Питомец:
- кошка Юта
""".strip()


@dataclass(slots=True)
class SelfTestResult:
    ok: bool
    score: int
    details: str


class StartupSelfTestService:
    def __init__(self, settings: Settings, dialogue: DialogueService, llm: ChadAIClient) -> None:
        self.settings = settings
        self.dialogue = dialogue
        self.llm = llm

    async def _ask_bot(self) -> dict[str, str]:
        prompts: dict[str, str] = {
            "zahar": "Что ты знаешь о Захаре? Дай факты кратко списком.",
            "katya": "Что ты знаешь о Кате? Дай факты кратко списком.",
            "sofa": "Что ты знаешь о Софе? Дай факты кратко списком.",
            "relations": "Что ты знаешь об их отношениях и питомце?",
        }
        answers: dict[str, str] = {}
        synthetic_chat_id = -900_000_001
        synthetic_user_id = 752142337
        for key, prompt in prompts.items():
            logger.info("startup_selftest_prompt key=%s prompt=%r", key, prompt)
            answers[key] = await self.dialogue.answer(
                user_id=synthetic_user_id,
                chat_id=synthetic_chat_id,
                user_text=prompt,
                context_hint="startup_selftest",
                is_group_chat=False,
                persist=False,
            )
            logger.info("startup_selftest_answer key=%s answer=%r", key, answers[key])
        return answers

    async def _validate_answers(self, answers: dict[str, str]) -> SelfTestResult:
        validation_prompt = (
            "Ты валидатор качества памяти ассистента. "
            "Сравни ответы ассистента с эталоном. "
            "Верни строго JSON формата: "
            '{"ok": boolean, "score": 0..100, "missing": [string], "wrong": [string], "summary": string}.'
        )
        validation_input = (
            "ЭТАЛОН:\n"
            f"{GROUND_TRUTH}\n\n"
            "ОТВЕТЫ АССИСТЕНТА:\n"
            f"{json.dumps(answers, ensure_ascii=False, indent=2)}\n\n"
            "Правила оценки:\n"
            "- Считай критичными ошибки по дате рождения, ролям в мастерской, отношениям и кошке Юте.\n"
            "- Если пропущен хотя бы один критичный факт, ok=false.\n"
            "- Если есть выдуманные факты, ok=false.\n"
            "- score 100 только при полном совпадении по сути."
        )
        raw = await self.llm.complete(
            messages=[
                {"role": "system", "content": validation_prompt},
                {"role": "user", "content": validation_input},
            ],
            model=self.settings.startup_selftest_validator_model,
            timeout_seconds=self.settings.startup_selftest_timeout_seconds,
            max_tokens=100000,
        )
        logger.info("startup_selftest_validator_raw response=%r", raw)
        try:
            payload = json.loads(raw)
            ok = bool(payload.get("ok", False))
            score = int(payload.get("score", 0))
            details = json.dumps(payload, ensure_ascii=False)
            return SelfTestResult(ok=ok, score=max(0, min(100, score)), details=details)
        except Exception as exc:
            logger.error("startup_selftest_validator_parse_error error=%s raw=%r", exc, raw)
            return SelfTestResult(ok=False, score=0, details=f"Validator parse failed: {exc}")

    async def run(self) -> SelfTestResult:
        answers = await self._ask_bot()
        result = await self._validate_answers(answers)
        logger.info("startup_selftest_result ok=%s score=%s details=%s", result.ok, result.score, result.details)
        return result
