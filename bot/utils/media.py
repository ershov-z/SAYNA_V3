from __future__ import annotations

import base64
import io
import logging
import mimetypes

from aiogram import Bot
from aiogram.types import Message

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGES_PER_MESSAGE = 5


def _data_url(payload: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


async def extract_message_images(bot: Bot, message: Message) -> list[str]:
    """
    Extract up to 5 images from Telegram message as data URLs.
    Supports:
    - message.photo
    - message.document with image/* MIME type
    """
    images: list[str] = []

    photo = message.photo[-1] if message.photo else None
    if photo and len(images) < MAX_IMAGES_PER_MESSAGE:
        file_size = int(photo.file_size or 0)
        if file_size and file_size > MAX_IMAGE_BYTES:
            logger.warning("media_skip_photo_too_large chat_id=%s user_id=%s size=%s", message.chat.id, getattr(message.from_user, "id", None), file_size)
        else:
            try:
                file = await bot.get_file(photo.file_id)
                if file.file_size and int(file.file_size) > MAX_IMAGE_BYTES:
                    logger.warning(
                        "media_skip_photo_too_large_after_get chat_id=%s user_id=%s size=%s",
                        message.chat.id,
                        getattr(message.from_user, "id", None),
                        file.file_size,
                    )
                elif file.file_path:
                    buf = io.BytesIO()
                    await bot.download_file(file.file_path, destination=buf)
                    images.append(_data_url(buf.getvalue(), "image/jpeg"))
            except Exception as exc:
                logger.warning("media_photo_extract_failed chat_id=%s user_id=%s error=%s", message.chat.id, getattr(message.from_user, "id", None), exc)

    document = message.document
    if document and len(images) < MAX_IMAGES_PER_MESSAGE:
        mime_type = str(document.mime_type or "").lower()
        if mime_type.startswith("image/"):
            file_size = int(document.file_size or 0)
            if file_size and file_size > MAX_IMAGE_BYTES:
                logger.warning(
                    "media_skip_document_too_large chat_id=%s user_id=%s size=%s",
                    message.chat.id,
                    getattr(message.from_user, "id", None),
                    file_size,
                )
            else:
                try:
                    file = await bot.get_file(document.file_id)
                    if file.file_size and int(file.file_size) > MAX_IMAGE_BYTES:
                        logger.warning(
                            "media_skip_document_too_large_after_get chat_id=%s user_id=%s size=%s",
                            message.chat.id,
                            getattr(message.from_user, "id", None),
                            file.file_size,
                        )
                    elif file.file_path:
                        guessed, _ = mimetypes.guess_type(document.file_name or "")
                        final_mime = mime_type or guessed or "image/jpeg"
                        buf = io.BytesIO()
                        await bot.download_file(file.file_path, destination=buf)
                        images.append(_data_url(buf.getvalue(), final_mime))
                except Exception as exc:
                    logger.warning(
                        "media_document_extract_failed chat_id=%s user_id=%s error=%s",
                        message.chat.id,
                        getattr(message.from_user, "id", None),
                        exc,
                    )
    return images
