from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from bot.models.message_flow import MessageEnvelope
from bot.services.container import ServiceContainer
from bot.utils.media import extract_message_images

router = Router(name="private")
logger = logging.getLogger(__name__)
NETWORK_REPLY_RETRIES = 3


def setup_private_handlers(services: ServiceContainer) -> Router:
    async def safe_answer(message: Message, text: str) -> None:
        for attempt in range(1, NETWORK_REPLY_RETRIES + 1):
            try:
                await message.answer(text)
                return
            except TelegramBadRequest as exc:
                if "can't parse entities" not in str(exc):
                    raise
                logger.warning(
                    "private_fallback_to_plain_text chat_id=%s user_id=%s text=%r error=%s",
                    message.chat.id,
                    message.from_user.id if message.from_user else None,
                    text,
                    exc,
                )
                await message.answer(text, parse_mode=None)
                return
            except TelegramNetworkError as exc:
                if attempt >= NETWORK_REPLY_RETRIES:
                    logger.error(
                        "private_reply_network_failed chat_id=%s user_id=%s attempts=%s error=%s",
                        message.chat.id,
                        message.from_user.id if message.from_user else None,
                        attempt,
                        exc,
                    )
                    return
                backoff = 0.6 * attempt
                logger.warning(
                    "private_reply_network_retry chat_id=%s user_id=%s attempt=%s sleep=%.1fs error=%s",
                    message.chat.id,
                    message.from_user.id if message.from_user else None,
                    attempt,
                    backoff,
                    exc,
                )
                await asyncio.sleep(backoff)

    async def safe_answer_photo(message: Message, photo: str, caption: str | None) -> None:
        for attempt in range(1, NETWORK_REPLY_RETRIES + 1):
            try:
                await message.answer_photo(photo=photo, caption=caption)
                return
            except TelegramBadRequest as exc:
                if "can't parse entities" not in str(exc):
                    raise
                logger.warning(
                    "private_photo_fallback_to_plain_text chat_id=%s user_id=%s caption=%r error=%s",
                    message.chat.id,
                    message.from_user.id if message.from_user else None,
                    caption,
                    exc,
                )
                await message.answer_photo(photo=photo, caption=caption, parse_mode=None)
                return
            except TelegramNetworkError as exc:
                if attempt >= NETWORK_REPLY_RETRIES:
                    logger.error(
                        "private_photo_network_failed chat_id=%s user_id=%s attempts=%s error=%s",
                        message.chat.id,
                        message.from_user.id if message.from_user else None,
                        attempt,
                        exc,
                    )
                    return
                backoff = 0.6 * attempt
                logger.warning(
                    "private_photo_network_retry chat_id=%s user_id=%s attempt=%s sleep=%.1fs error=%s",
                    message.chat.id,
                    message.from_user.id if message.from_user else None,
                    attempt,
                    backoff,
                    exc,
                )
                await asyncio.sleep(backoff)

    @router.message(F.chat.type == "private", Command("start"))
    async def start(message: Message) -> None:
        await safe_answer(
            message,
            "Привет! Я ассистент мастерской.\n"
            "Примеры:\n"
            "- заказ Стол сумма 15000 дедлайн 2026-06-10 клиент Иван\n"
            "- прогресс ab12cd34 70 статус active\n"
            "- user2 должен помыть посуду до 2026-05-21\n"
            "- список заказов"
        )

    @router.message(F.chat.type == "private", Command("orders"))
    async def list_orders(message: Message) -> None:
        result = await services.task_order.try_handle_command("/orders", message.from_user.id)
        await safe_answer(message, result.text)

    @router.message(F.chat.type == "private", Command("todos"))
    async def list_todos(message: Message) -> None:
        result = await services.task_order.try_handle_command("/todos", message.from_user.id)
        await safe_answer(message, result.text)

    @router.message(F.chat.type == "private", Command("digest"))
    async def run_digest(message: Message) -> None:
        result = await services.task_order.try_handle_command("/digest", message.from_user.id)
        await safe_answer(message, result.text)

    @router.message(F.chat.type == "private")
    async def private_text(message: Message) -> None:
        user_text = (message.text or message.caption or "").strip()
        images = await extract_message_images(services.bot, message)
        if not user_text and not images:
            return
        if not user_text and images:
            user_text = "Опиши, пожалуйста, это изображение."
        logger.info(
            "private_message_in chat_id=%s user_id=%s images=%s text=%r",
            message.chat.id,
            message.from_user.id if message.from_user else None,
            len(images),
            user_text,
        )
        envelope = MessageEnvelope(
            user_id=message.from_user.id,
            chat_id=message.chat.id,
            text=user_text,
            user_display_name=message.from_user.full_name or (message.from_user.username or ""),
            images=images,
            is_group_chat=False,
            context_hint="private_chat",
        )
        async with ChatActionSender.typing(bot=services.bot, chat_id=message.chat.id):
            result = await services.orchestrator.process(envelope)
            if result.image_url:
                await safe_answer_photo(message, photo=result.image_url, caption=result.text or "Готово.")
            else:
                await safe_answer(message, result.text)

    return router
