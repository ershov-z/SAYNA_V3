from __future__ import annotations

import logging
import random
import re
import time

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from bot.models.message_flow import MessageEnvelope
from bot.services.container import ServiceContainer
from bot.utils.media import extract_message_images

router = Router(name="group")
logger = logging.getLogger(__name__)

TRIGGER_WORDS = (
    "сайна",
    "бот",
    "секретарь",
    "ассистент",
    "заказ",
    "дедлайн",
    "напомни",
    "кто должен",
    "прогресс",
)

BOT_ALIASES = (
    "сайна",
    "saina",
    "бот",
    "секретарь",
    "ассистент",
    "рободевка",
    "рободевочка",
)

ADDRESS_HINTS = (
    "запиши",
    "напомни",
    "скажи",
    "ответь",
    "что по",
    "посмотри",
    "проверь",
    "кто должен",
    "подскажи",
)


def setup_group_handlers(services: ServiceContainer) -> Router:
    last_reply_by_chat: dict[int, float] = {}
    active_until_by_chat: dict[int, float] = {}

    async def safe_reply(message: Message, text: str) -> None:
        try:
            await message.reply(text)
        except TelegramBadRequest as exc:
            if "can't parse entities" not in str(exc):
                raise
            logger.warning(
                "group_fallback_to_plain_text chat_id=%s user_id=%s text=%r error=%s",
                message.chat.id,
                message.from_user.id if message.from_user else None,
                text,
                exc,
            )
            await message.reply(text, parse_mode=None)

    async def safe_reply_photo(message: Message, photo: str, caption: str | None) -> None:
        try:
            await message.reply_photo(photo=photo, caption=caption)
        except TelegramBadRequest as exc:
            if "can't parse entities" not in str(exc):
                raise
            logger.warning(
                "group_photo_fallback_to_plain_text chat_id=%s user_id=%s caption=%r error=%s",
                message.chat.id,
                message.from_user.id if message.from_user else None,
                caption,
                exc,
            )
            await message.reply_photo(photo=photo, caption=caption, parse_mode=None)

    def _mentions_bot(text: str, bot_username: str | None) -> bool:
        lowered = text.lower()
        if bot_username and f"@{bot_username.lower()}" in lowered:
            return True
        return any(word in lowered for word in BOT_ALIASES)

    def _contains_triggers(text: str) -> bool:
        lowered = text.lower()
        return any(word in lowered for word in TRIGGER_WORDS)

    def _is_direct_address(text: str, bot_username: str | None) -> bool:
        lowered = text.lower().strip()
        if bot_username and lowered.startswith(f"@{bot_username.lower()}"):
            return True
        alias_prefix = tuple(f"{alias}," for alias in BOT_ALIASES) + tuple(f"{alias} " for alias in BOT_ALIASES)
        if lowered.startswith(alias_prefix):
            return True
        if any(hint in lowered for hint in ADDRESS_HINTS) and any(alias in lowered for alias in BOT_ALIASES):
            return True
        return False

    def _is_question(text: str) -> bool:
        return bool(re.search(r"\?$", text.strip()))

    @router.message(F.chat.type.in_({"group", "supergroup"}))
    async def group_text(message: Message) -> None:
        text = (message.text or message.caption or "").strip()
        has_media = bool(message.photo or (message.document and str(message.document.mime_type or "").lower().startswith("image/")))
        if (not text and not has_media) or not message.from_user:
            return
        image_decision = await services.image_intent.score(text) if text else None
        image_generate_needed = bool(image_decision and image_decision.should_generate)
        intent_text = text or "[media]"
        logger.info(
            "group_message_in chat_id=%s user_id=%s has_media=%s text=%r",
            message.chat.id,
            message.from_user.id,
            has_media,
            intent_text,
        )

        bot_user = await services.bot.get_me()
        mentioned = _mentions_bot(intent_text, bot_user.username)
        replied_to_bot = bool(message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == bot_user.id)
        direct_address = _is_direct_address(intent_text, bot_user.username)
        trigger = _contains_triggers(intent_text)
        question = _is_question(intent_text)
        intent_score = await services.intent_scorer.score_group_message(
            chat_id=message.chat.id,
            message_text=intent_text,
            bot_username=bot_user.username,
            replied_to_bot=replied_to_bot,
        )
        llm_addressed = intent_score >= services.settings.group_intent_score_threshold
        logger.info(
            "group_intent_eval chat_id=%s user_id=%s score=%s threshold=%s llm_addressed=%s image_score=%s image_needed=%s mentioned=%s replied=%s direct=%s trigger=%s question=%s",
            message.chat.id,
            message.from_user.id,
            intent_score,
            services.settings.group_intent_score_threshold,
            llm_addressed,
            image_decision.score if image_decision else 0,
            image_generate_needed,
            mentioned,
            replied_to_bot,
            direct_address,
            trigger,
            question,
        )

        now = time.time()
        cooldown_until = last_reply_by_chat.get(message.chat.id, 0.0)
        is_active_window = now < active_until_by_chat.get(message.chat.id, 0.0)

        if now < cooldown_until and not (mentioned or replied_to_bot or direct_address or llm_addressed or image_generate_needed):
            logger.info("group_skip_cooldown chat_id=%s user_id=%s", message.chat.id, message.from_user.id)
            return

        should_answer = False
        context_hint = "group_chat"
        if mentioned or replied_to_bot or direct_address:
            should_answer = True
            context_hint = "direct_address"
            active_until_by_chat[message.chat.id] = now + services.settings.group_active_window_seconds
        elif llm_addressed:
            should_answer = True
            context_hint = f"pre_score_{intent_score}"
            active_until_by_chat[message.chat.id] = now + services.settings.group_active_window_seconds
        elif is_active_window and (trigger or question):
            if trigger or random.random() < services.settings.group_active_context_probability:
                should_answer = True
                context_hint = "active_dialogue"
        elif image_generate_needed:
            should_answer = True
            context_hint = "image_generation"
        elif trigger and question:
            should_answer = True
            context_hint = "task_discussion"
        elif trigger and random.random() < services.settings.group_context_probability:
            should_answer = True
            context_hint = "smart_participation"

        if not should_answer:
            logger.info("group_skip_not_addressed chat_id=%s user_id=%s", message.chat.id, message.from_user.id)
            return
        logger.info("group_answer_decision chat_id=%s user_id=%s context_hint=%s", message.chat.id, message.from_user.id, context_hint)

        images = await extract_message_images(services.bot, message)
        prompt_text = text
        if not prompt_text:
            prompt_text = "Опиши, пожалуйста, это изображение."
        reply_to_text = ""
        reply_to_user_id: int | None = None
        if message.reply_to_message and (message.reply_to_message.text or message.reply_to_message.caption):
            reply_to_text = (message.reply_to_message.text or message.reply_to_message.caption or "")
            if message.reply_to_message.from_user:
                reply_to_user_id = message.reply_to_message.from_user.id

        envelope = MessageEnvelope(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            text=prompt_text,
            user_display_name=message.from_user.full_name or (message.from_user.username or ""),
            images=images,
            is_group_chat=True,
            context_hint=context_hint,
            reply_to_text=reply_to_text,
            reply_to_user_id=reply_to_user_id,
        )
        async with ChatActionSender.typing(bot=services.bot, chat_id=message.chat.id):
            result = await services.orchestrator.process(envelope)
            if result.image_url:
                await safe_reply_photo(message, result.image_url, result.text or "Готово.")
            else:
                await safe_reply(message, result.text)

        last_reply_by_chat[message.chat.id] = now + services.settings.group_cooldown_seconds

    return router
