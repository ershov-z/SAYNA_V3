from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

try:
    import msvcrt  # type: ignore
except ImportError:  # pragma: no cover
    msvcrt = None

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover
    fcntl = None

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from bot.config import get_settings
from bot.handlers import build_router
from bot.logging_setup import configure_logging
from bot.middleware.access_control import AccessControlMiddleware
from bot.middleware.sequential_processing import SequentialProcessingMiddleware
from bot.scheduler.jobs import build_scheduler
from bot.services.container import build_services
from bot.services.reminders import ReminderService
from scripts.seed_memory_profiles import seed_if_needed

logger = logging.getLogger(__name__)


class ProcessLock:
    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._fp = None

    def acquire(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = self.lock_path.open("a+", encoding="utf-8")
        try:
            if msvcrt is not None:
                self._fp.seek(0)
                msvcrt.locking(self._fp.fileno(), msvcrt.LK_NBLCK, 1)
            elif fcntl is not None:  # pragma: no cover
                fcntl.flock(self._fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:  # pragma: no cover
                return True
            self._fp.seek(0)
            self._fp.truncate()
            self._fp.write(str(os.getpid()))
            self._fp.flush()
            return True
        except OSError:
            self.release()
            return False

    def release(self) -> None:
        if self._fp is None:
            return
        try:
            if msvcrt is not None:
                self._fp.seek(0)
                msvcrt.locking(self._fp.fileno(), msvcrt.LK_UNLCK, 1)
            elif fcntl is not None:  # pragma: no cover
                fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            self._fp.close()
        finally:
            self._fp = None


async def _run_startup_selftest_background(services) -> None:
    try:
        result = await services.startup_selftest.run()
        if not result.ok:
            logger.error("Startup self-test FAILED score=%s details=%s", result.score, result.details)
        else:
            logger.info("Startup self-test passed score=%s", result.score)
    except Exception:
        logger.exception("Startup self-test task crashed")


async def _send_startup_announcement(services, bot: Bot, digest_chat_id: int) -> None:
    startup_messages = [
        {"role": "system", "content": services.soul.persona},
        {"role": "system", "content": services.soul.module_style_prompt("chat")},
        {
            "role": "system",
            "content": (
                "Ты только что перезапустилась и возвращаешься в общий чат. "
                "Сформулируй короткое живое сообщение в стиле персонажа: "
                "1-2 предложения, дружелюбно, без технических деталей, без markdown."
            ),
        },
        {
            "role": "user",
            "content": "Напиши сообщение в общий чат о том, что ты снова на связи и готова помогать.",
        },
    ]
    startup_raw = await services.llm.complete(startup_messages, timeout_seconds=10.0, max_tokens=400)
    startup_text = services.soul.finalize_reply(startup_raw).strip()
    if not startup_text:
        startup_text = "Я снова на связи и готова помогать в общем чате."
    try:
        await bot.send_message(digest_chat_id, startup_text)
    except TelegramBadRequest as exc:
        if "can't parse entities" not in str(exc):
            raise
        logger.warning(
            "startup_announcement_fallback_to_plain_text chat_id=%s text=%r error=%s",
            digest_chat_id,
            startup_text,
            exc,
        )
        await bot.send_message(digest_chat_id, startup_text, parse_mode=None)
    await services.memory.remember("assistant", user_id=0, chat_id=digest_chat_id, text=startup_text)
    logger.info("startup_announcement_sent chat_id=%s", digest_chat_id)


async def main() -> None:
    configure_logging()
    settings = get_settings()
    process_lock = ProcessLock(Path("data/runtime/bot.lock"))
    if not process_lock.acquire():
        logger.error("Another bot instance is already running. Exiting to avoid polling/memory conflicts.")
        return

    bot = Bot(token=settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.update.middleware(AccessControlMiddleware(settings))
    dp.update.middleware(SequentialProcessingMiddleware())

    services = build_services(settings, bot)
    dp.include_router(build_router(services))

    # Always do only Google availability startup check by default.
    await services.sheets.check_google_availability()

    seeded_entries = seed_if_needed(settings)
    if seeded_entries:
        logger.info("Initial memory seed completed entries=%s", seeded_entries)
    else:
        logger.info("Initial memory seed skipped (already initialized or disabled)")

    reminders = ReminderService(services.sheets, bot, llm=services.llm, soul=services.soul)
    scheduler = build_scheduler(settings, reminders, services.memory, services.digest)
    scheduler.start()

    startup_tasks: list[asyncio.Task[None]] = []

    if settings.startup_selftest_enabled:
        if settings.startup_selftest_fail_fast:
            logger.info("Startup self-test enabled (fail-fast), running blocking validation check")
            result = await services.startup_selftest.run()
            if not result.ok:
                logger.error("Startup self-test FAILED score=%s details=%s", result.score, result.details)
                raise RuntimeError(f"Startup self-test failed: {result.details}")
            logger.info("Startup self-test passed score=%s", result.score)
        else:
            logger.info("Startup self-test enabled, scheduling background memory validation check")
            startup_tasks.append(asyncio.create_task(_run_startup_selftest_background(services)))

    if settings.digest_chat_id != 0:
        logger.info("Scheduling startup announcement in background chat_id=%s", settings.digest_chat_id)
        startup_tasks.append(
            asyncio.create_task(_send_startup_announcement(services, bot, settings.digest_chat_id))
        )
    else:
        logger.info("startup_announcement_skipped_missing_digest_chat_id")

    logger.info("Bot started")
    try:
        await dp.start_polling(bot)
    finally:
        for task in startup_tasks:
            task.cancel()
        if startup_tasks:
            await asyncio.gather(*startup_tasks, return_exceptions=True)
        scheduler.shutdown(wait=False)
        await services.image.close()
        await services.llm.close()
        await bot.session.close()
        process_lock.release()


if __name__ == "__main__":
    asyncio.run(main())
