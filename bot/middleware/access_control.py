from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, Update

from bot.config import Settings

logger = logging.getLogger(__name__)


class AccessControlMiddleware(BaseMiddleware):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        message: Message | None = data.get("event_message")
        if message is None:
            return await handler(event, data)

        from_user = message.from_user
        if from_user is None or from_user.id not in self.settings.allowed_user_ids:
            logger.warning("Denied user_id=%s chat_id=%s", getattr(from_user, "id", None), message.chat.id)
            return None

        chat_id = message.chat.id
        if message.chat.type in {"group", "supergroup"} and chat_id not in self.settings.allowed_chat_ids:
            logger.warning("Denied chat_id=%s for user_id=%s", chat_id, from_user.id)
            return None

        return await handler(event, data)
