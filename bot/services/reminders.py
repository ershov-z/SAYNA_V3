from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
import logging

from aiogram import Bot

from bot.services.sheets import GoogleSheetsService

logger = logging.getLogger(__name__)
FOLLOWUP_LEAD_MINUTES = 10


class ReminderService:
    def __init__(self, sheets: GoogleSheetsService, bot: Bot) -> None:
        self.sheets = sheets
        self.bot = bot

    @staticmethod
    def _normalize_name(value: object) -> str:
        cleaned = re.sub(r"[^a-zA-Zа-яА-ЯёЁ0-9_]+", "", str(value or "").lower())
        return cleaned

    @staticmethod
    def _parse_datetime_utc(raw_value: object) -> datetime | None:
        text = str(raw_value or "").strip()
        if not text:
            return None
        text = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            logger.warning("failed_to_parse_datetime raw=%r", raw_value)
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _resolve_recipients(self, order: dict) -> list[int]:
        owner_raw = order.get("owner_user_id")
        if owner_raw not in (None, ""):
            try:
                return [int(owner_raw)]
            except (TypeError, ValueError):
                pass

        responsible_raw = str(order.get("responsible", "")).strip()
        if not responsible_raw:
            return []

        name_map = {
            "захар": 752142337,
            "zahar": 752142337,
            "я": 752142337,
            "меня": 752142337,
            "мне": 752142337,
            "мой": 752142337,
            "моя": 752142337,
            "катя": 495538754,
            "katya": 495538754,
            "софа": 381448542,
            "sofa": 381448542,
            "sofia": 381448542,
            "sofaa": 381448542,
        }
        normalized = self._normalize_name(responsible_raw)
        if normalized in name_map:
            return [name_map[normalized]]

        logger.warning(
            "reminder_recipient_unresolved order_id=%s responsible=%r",
            order.get("order_id", ""),
            responsible_raw,
        )
        return []

    async def send_order_progress_ping(self) -> None:
        orders = await self.sheets.list_active_orders()
        owners: dict[int, list[dict]] = {}
        for order in orders:
            recipients = self._resolve_recipients(order)
            for recipient in recipients:
                owners.setdefault(recipient, []).append(order)

        for user_id, user_orders in owners.items():
            lines = ["Ежедневный чек по заказам. Обнови прогресс, пожалуйста:"]
            for order in user_orders:
                lines.append(
                    f"- {order['title']} (ID: {order['order_id']}), дедлайн: {order['due_date']}, прогресс: {order['progress_percent']}%"
                )
            await self.bot.send_message(user_id, "\n".join(lines))

    async def send_deadline_alerts(self) -> None:
        now = datetime.now(timezone.utc)
        orders = await self.sheets.list_active_orders()
        for order in orders:
            try:
                due_at = datetime.fromisoformat(str(order.get("due_date", "")))
            except ValueError:
                continue
            progress = int(order.get("progress_percent", 0))
            hours_left = (due_at - now).total_seconds() / 3600
            if (hours_left < 24 and progress < 80) or (hours_left < 0 and progress < 100):
                recipients = self._resolve_recipients(order)
                for recipient in recipients:
                    await self.bot.send_message(
                        recipient,
                        (
                            "Риск по сроку заказа.\n"
                            f"- {order['title']} (ID: {order['order_id']})\n"
                            f"- Дедлайн: {order['due_date']}\n"
                            f"- Прогресс: {progress}%"
                        ),
                    )

    async def send_todo_reminders(self) -> None:
        todos = await self.sheets.list_open_todos()
        now = datetime.now(timezone.utc)
        for todo in todos:
            due_at = self._parse_datetime_utc(todo.get("due_at", ""))
            last_reminded_at = self._parse_datetime_utc(todo.get("last_reminded_at", ""))
            to_user = int(todo["to_user_id"])
            from_user = int(todo["from_user_id"])

            if last_reminded_at is None:
                due_hint = f"\nДедлайн: {due_at.isoformat()}" if due_at is not None else ""
                await self.bot.send_message(
                    to_user,
                    f"Новая просьба от user{from_user}: {todo['text']}{due_hint}",
                )
                await self.sheets.mark_todo_reminded(str(todo["todo_id"]))
                continue

            if due_at is None:
                continue

            followup_window_start = due_at - timedelta(minutes=FOLLOWUP_LEAD_MINUTES)
            seconds_left = (due_at - now).total_seconds()
            should_send_followup = 0 < seconds_left <= FOLLOWUP_LEAD_MINUTES * 60 and last_reminded_at < followup_window_start
            if should_send_followup:
                await self.bot.send_message(
                    to_user,
                    (
                        f"Через {FOLLOWUP_LEAD_MINUTES} минут дедлайн по просьбе от user{from_user}: {todo['text']}\n"
                        "Получилось выполнить?"
                    ),
                )
                await self.sheets.mark_todo_reminded(str(todo["todo_id"]))
