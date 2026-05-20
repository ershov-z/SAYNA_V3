from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, Update

logger = logging.getLogger(__name__)


class SequentialProcessingMiddleware(BaseMiddleware):
    """
    Enforce strict single-request processing across the bot.

    Only one incoming message is processed at a time end-to-end
    (including task handling, dialogue generation and memory writes).
    """

    def __init__(self) -> None:
        super().__init__()
        self._lock = asyncio.Lock()

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        message: Message | None = data.get("event_message")
        if message is None:
            return await handler(event, data)

        logger.info(
            "sequential_queue_wait chat_id=%s user_id=%s",
            message.chat.id,
            getattr(message.from_user, "id", None),
        )
        async with self._lock:
            logger.info(
                "sequential_queue_acquired chat_id=%s user_id=%s",
                message.chat.id,
                getattr(message.from_user, "id", None),
            )
            return await handler(event, data)
