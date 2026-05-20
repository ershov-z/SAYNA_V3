from __future__ import annotations

import logging
import re

from bot.config import Settings
from bot.services.chad_ai import ChadAIClient
from bot.services.memory import MemPalaceService
from bot.services.soul import SoulService

logger = logging.getLogger(__name__)


class DialogueService:
    def __init__(self, settings: Settings, llm: ChadAIClient, memory: MemPalaceService, soul: SoulService) -> None:
        self.settings = settings
        self.llm = llm
        self.memory = memory
        self.soul = soul
        self._last_target_user_by_chat: dict[int, int] = {}

    USER_ALIASES: dict[int, tuple[str, ...]] = {
        752142337: ("захар", "zahar", "@fenptropill_cosplay", "fenptropill_cosplay"),
        495538754: ("катя", "katya", "@tenebris_cosplay", "tenebris_cosplay"),
        381448542: ("софа", "софе", "софу", "софой", "софы", "софочка", "софушечка", "sofa", "@salmo_salar", "salmo_salar"),
    }

    USER_NAME_PATTERNS: dict[int, tuple[str, ...]] = {
        752142337: (r"\bзахар\w*\b",),
        495538754: (r"\bкат[яеию]\w*\b",),
        381448542: (r"\bсоф[аеуыой]\w*\b", r"\bсофочк\w*\b", r"\bсофушечк\w*\b"),
    }

    PERSON_PRONOUN_PATTERNS = (
        r"\bеё\b",
        r"\bее\b",
        r"\bеёшн\w*\b",
        r"\bеешн\w*\b",
        r"\bего\b",
        r"\bеё\s+\w+\b",
        r"\bее\s+\w+\b",
    )

    @staticmethod
    def _is_personal_query(text: str) -> bool:
        lowered = text.lower()
        return any(
            token in lowered
            for token in (
                "обо мне",
                "про меня",
                "что ты знаешь обо мне",
                "что ты знаешь про меня",
                "мои",
                "мой",
                "мне",
            )
        )

    def _referenced_user_ids(self, text: str, current_user_id: int, is_group_chat: bool) -> list[int]:
        lowered = text.lower()
        result: list[int] = []
        for uid, aliases in self.USER_ALIASES.items():
            if any(alias in lowered for alias in aliases):
                result.append(uid)
                continue
            patterns = self.USER_NAME_PATTERNS.get(uid, ())
            if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in patterns):
                result.append(uid)
        if not result and self._is_personal_query(text):
            result.append(current_user_id)
        # In private chat, personal fallback should always include the current user.
        if not is_group_chat and current_user_id not in result:
            result.append(current_user_id)
        return list(dict.fromkeys(result))

    def _resolve_target_users(
        self,
        *,
        chat_id: int,
        user_text: str,
        current_user_id: int,
        is_group_chat: bool,
        reply_to_user_id: int | None,
    ) -> list[int]:
        referenced = self._referenced_user_ids(user_text, current_user_id=current_user_id, is_group_chat=is_group_chat)
        if reply_to_user_id and reply_to_user_id in self.USER_ALIASES:
            referenced.insert(0, reply_to_user_id)
        has_pronoun = any(re.search(p, user_text.lower(), flags=re.IGNORECASE) for p in self.PERSON_PRONOUN_PATTERNS)
        if not referenced and has_pronoun and chat_id in self._last_target_user_by_chat:
            referenced.append(self._last_target_user_by_chat[chat_id])
        referenced = list(dict.fromkeys(referenced))
        if referenced:
            self._last_target_user_by_chat[chat_id] = referenced[0]
        return referenced

    async def answer(
        self,
        user_id: int,
        chat_id: int,
        user_text: str,
        images: list[str] | None = None,
        context_hint: str = "",
        is_group_chat: bool = False,
        reply_to_text: str = "",
        reply_to_user_id: int | None = None,
        prefetched_memory_context: str = "",
        persist: bool = True,
    ) -> str:
        logger.info(
            "dialogue_answer_start chat_id=%s user_id=%s is_group=%s hint=%s text=%r",
            chat_id,
            user_id,
            is_group_chat,
            context_hint,
            user_text,
        )
        recent_chat = await self.memory.get_recent_chat_messages(chat_id=chat_id, limit=15)
        user_profile = await self.memory.get_user_profile_context(user_id=user_id)
        referenced_user_ids = self._resolve_target_users(
            chat_id=chat_id,
            user_text=user_text,
            current_user_id=user_id,
            is_group_chat=is_group_chat,
            reply_to_user_id=reply_to_user_id,
        )
        referenced_profiles: list[tuple[int, str]] = []
        for referenced_id in referenced_user_ids:
            profile = await self.memory.get_user_profile_context(user_id=referenced_id)
            if profile:
                referenced_profiles.append((referenced_id, profile))
        memory_context = await self.memory.search_context(
            user_text,
            user_id=None,
            limit=3,
            chat_id=chat_id,
            fallback_user_ids=referenced_user_ids,
        )
        if prefetched_memory_context.strip():
            combined_lines = [line.strip() for line in prefetched_memory_context.splitlines() if line.strip()]
            combined_lines.extend(line.strip() for line in memory_context.splitlines() if line.strip())
            unique_lines = list(dict.fromkeys(combined_lines))
            memory_context = "\n".join(unique_lines[:8])
        logger.info(
            "dialogue_context_ready chat_id=%s user_id=%s referenced_users=%s recent_count=%s user_profile=%s memory_context_chars=%s",
            chat_id,
            user_id,
            referenced_user_ids,
            len(recent_chat),
            bool(user_profile),
            len(memory_context),
        )
        messages = [
            {"role": "system", "content": self.soul.persona},
            {"role": "system", "content": self.soul.module_style_prompt("chat")},
            {
                "role": "system",
                "content": (
                    "Ты в середине живого диалога. Держи непрерывность, учитывай последние реплики и не сбрасывай контекст "
                    "без явной просьбы пользователя."
                ),
            },
        ]
        if is_group_chat:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Это общий групповой чат с несколькими людьми. "
                        "Не веди отдельные ветки по пользователям. "
                        "Считай, что есть один общий разговор, где важно кто кому ответил. "
                        "Если сообщение было reply, сначала учитывай именно этот локальный контекст."
                    ),
                }
            )
        if context_hint:
            messages.append({"role": "system", "content": f"Контекст задачи: {context_hint}"})
        if user_profile:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Профиль текущего собеседника (это именно тот пользователь, который пишет прямо сейчас):\n"
                        f"{user_profile}"
                    ),
                }
            )
        if referenced_profiles:
            joined = "\n\n".join(f"user_{uid}:\n{profile}" for uid, profile in referenced_profiles)
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Профили пользователей, явно упомянутых в текущем вопросе. "
                        "Если вопрос про конкретного человека, используй прежде всего этот блок:\n"
                        f"{joined}"
                    ),
                }
            )
        if memory_context:
            messages.append({"role": "system", "content": f"Релевантная память:\n{memory_context}"})
        if reply_to_text.strip():
            target = f"user_{reply_to_user_id}" if reply_to_user_id else "unknown_user"
            messages.append(
                {
                    "role": "system",
                    "content": f"Текущее сообщение является reply к [{target}]: {reply_to_text.strip()[:500]}",
                }
            )
        if recent_chat:
            messages.extend(recent_chat)
        messages.append({"role": "user", "content": user_text})

        reply = await self.llm.complete(messages, images=images or [])
        reply = self.soul.finalize_reply(reply)
        logger.info("dialogue_answer_ready chat_id=%s user_id=%s reply_len=%s reply=%r", chat_id, user_id, len(reply), reply)
        if persist:
            await self.memory.remember("user", user_id, chat_id, user_text)
            await self.memory.remember("assistant", user_id, chat_id, reply)
        return reply
