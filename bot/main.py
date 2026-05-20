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

    seeded_entries = seed_if_needed(settings)
    if seeded_entries:
        logger.info("Initial memory seed completed entries=%s", seeded_entries)
    else:
        logger.info("Initial memory seed skipped (already initialized or disabled)")

    reminders = ReminderService(services.sheets, bot)
    scheduler = build_scheduler(settings, reminders, services.memory)
    scheduler.start()

    if settings.startup_selftest_enabled:
        logger.info("Startup self-test enabled, running memory validation check")
        result = await services.startup_selftest.run()
        if not result.ok:
            logger.error("Startup self-test FAILED score=%s details=%s", result.score, result.details)
            if settings.startup_selftest_fail_fast:
                raise RuntimeError(f"Startup self-test failed: {result.details}")
        else:
            logger.info("Startup self-test passed score=%s", result.score)

    logger.info("Bot started")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await services.image.close()
        await services.llm.close()
        await bot.session.close()
        process_lock.release()


if __name__ == "__main__":
    asyncio.run(main())
