from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo

from bot.config import Settings
from bot.services.digest import DigestService
from bot.services.memory import MemPalaceService
from bot.services.reminders import ReminderService

logger = logging.getLogger(__name__)


def build_scheduler(
    settings: Settings,
    reminders: ReminderService,
    memory: MemPalaceService,
    digest: DigestService,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(settings.timezone))

    scheduler.add_job(
        reminders.send_order_progress_ping,
        trigger=CronTrigger(hour=settings.daily_progress_check_hour, minute=settings.daily_progress_check_minute),
        id="daily_progress_ping",
        replace_existing=True,
    )
    scheduler.add_job(
        reminders.send_deadline_alerts,
        trigger="interval",
        hours=3,
        id="deadline_alerts",
        replace_existing=True,
    )
    scheduler.add_job(
        reminders.send_todo_reminders,
        trigger="interval",
        minutes=settings.reminder_interval_minutes,
        id="todo_reminders",
        replace_existing=True,
    )
    scheduler.add_job(
        digest.send_daily_digest,
        trigger=CronTrigger(hour=settings.daily_digest_hour, minute=settings.daily_digest_minute),
        id="daily_digest",
        replace_existing=True,
    )
    scheduler.add_job(
        memory.sweep,
        trigger="interval",
        hours=6,
        id="mempalace_sweep",
        replace_existing=True,
    )
    logger.info("Scheduler configured with recurring jobs")
    return scheduler
