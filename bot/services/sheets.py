from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import gspread
from gspread import Worksheet
from gspread.utils import rowcol_to_a1

from bot.config import Settings

logger = logging.getLogger(__name__)

ORDER_HEADERS = [
    "Название заказа",
    "Клиент",
    "Ответственный",
    "Стоимость заказа",
    "Стоимость материалов",
    "Дедлайн",
    "Готовность (в процентах)",
    "Сторипоинты",
]

ORDER_FIELD_TO_HEADER = {
    "title": "Название заказа",
    "client": "Клиент",
    "responsible": "Ответственный",
    "order_amount": "Стоимость заказа",
    "materials_amount": "Стоимость материалов",
    "due_date": "Дедлайн",
    "progress_percent": "Готовность (в процентах)",
    "story_points": "Сторипоинты",
}

TODO_HEADERS = [
    "todo_id",
    "from_user_id",
    "to_user_id",
    "text",
    "due_at",
    "priority",
    "status",
    "last_reminded_at",
]

EVENT_HEADERS = [
    "event_id",
    "owner_user_id",
    "title",
    "datetime",
    "notes",
    "remind_before_min",
]


class GoogleSheetsService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._enabled = False
        self._orders_mem: list[dict[str, Any]] = []
        self._todos_mem: list[dict[str, Any]] = []
        self._events_mem: list[dict[str, Any]] = []
        self.orders_sheet: Worksheet | None = None
        self.todos_sheet: Worksheet | None = None
        self.events_sheet: Worksheet | None = None

        cred_file = Path(settings.google_service_account_file)
        missing_sheet_id = not settings.google_sheet_id or settings.google_sheet_id == "replace_with_sheet_id"
        if missing_sheet_id or not cred_file.exists():
            logger.warning(
                "Google Sheets disabled: configure GOOGLE_SHEET_ID and credentials file. Using in-memory fallback storage."
            )
            return
        try:
            self._client = gspread.service_account(filename=settings.google_service_account_file)
            self._spreadsheet = self._client.open_by_key(settings.google_sheet_id)
            self.orders_sheet = self._get_or_create_sheet("orders", ORDER_HEADERS)
            self.todos_sheet = self._get_or_create_sheet("todos", TODO_HEADERS)
            self.events_sheet = self._get_or_create_sheet("events", EVENT_HEADERS)
            self._enabled = True
        except Exception as exc:
            logger.warning("Google Sheets unavailable (%s). Using in-memory fallback storage.", exc)

    def _get_or_create_sheet(self, title: str, headers: list[str]) -> Worksheet:
        try:
            worksheet = self._spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            worksheet = self._spreadsheet.add_worksheet(title=title, rows=200, cols=len(headers) + 2)
        first_row = worksheet.row_values(1)
        if first_row != headers:
            worksheet.update("A1", [headers], value_input_option="RAW")
        return worksheet

    @staticmethod
    def _order_id_from_row_index(row_idx: int) -> str:
        return f"r{row_idx:04d}"

    @staticmethod
    def _row_index_from_order_id(order_id: str) -> int | None:
        cleaned = str(order_id or "").strip().lower()
        if not cleaned.startswith("r"):
            return None
        try:
            return int(cleaned[1:])
        except ValueError:
            return None

    def _normalize_order_row(self, row: dict[str, Any], row_idx: int) -> dict[str, Any]:
        title = str(row.get(ORDER_FIELD_TO_HEADER["title"], "")).strip()
        client = str(row.get(ORDER_FIELD_TO_HEADER["client"], "")).strip()
        responsible = str(row.get(ORDER_FIELD_TO_HEADER["responsible"], "")).strip()
        due_date_raw = row.get(ORDER_FIELD_TO_HEADER["due_date"], "")
        due_date = self._parse_due_datetime(due_date_raw)
        progress_raw = str(row.get(ORDER_FIELD_TO_HEADER["progress_percent"], "0")).strip()
        story_points_raw = str(row.get(ORDER_FIELD_TO_HEADER["story_points"], "0")).strip()
        order_amount_raw = str(row.get(ORDER_FIELD_TO_HEADER["order_amount"], "0")).strip().replace(",", ".")
        materials_raw = str(row.get(ORDER_FIELD_TO_HEADER["materials_amount"], "0")).strip().replace(",", ".")
        try:
            progress = max(0, min(100, int(float(progress_raw or "0"))))
        except ValueError:
            progress = 0
        try:
            story_points = int(float(story_points_raw or "0"))
        except ValueError:
            story_points = 0
        try:
            order_amount = float(order_amount_raw or "0")
        except ValueError:
            order_amount = 0.0
        try:
            materials_amount = float(materials_raw or "0")
        except ValueError:
            materials_amount = 0.0
        return {
            "order_id": self._order_id_from_row_index(row_idx),
            "title": title,
            "client": client,
            "responsible": responsible,
            "amount": order_amount,
            "materials_amount": materials_amount,
            "due_date": due_date.isoformat() if due_date else str(due_date_raw or ""),
            "progress_percent": progress,
            "story_points": story_points,
        }

    @staticmethod
    def _parse_due_datetime(raw: Any) -> datetime | None:
        text = str(raw or "").strip()
        if not text:
            return None
        # Some sheet/date parsers produce hour as single digit: "2026-06-10 0:00:00".
        text = text.replace("T", " ")
        text = text.replace("Z", "+00:00")
        text = re.sub(r"^(\d{4}-\d{2}-\d{2})\s(\d):", r"\1 0\2:", text)
        if len(text) == 10 and text.count("-") == 2:
            text = f"{text} 00:00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            logger.warning("Failed to parse due_date=%r", raw)
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    async def add_order(
        self,
        title: str,
        client: str,
        amount: float,
        owner_user_id: int,
        due_date: datetime,
        *,
        responsible: str = "",
        materials_amount: float = 0.0,
        progress_percent: int = 0,
        story_points: int = 0,
    ) -> dict[str, Any]:
        order = {
            ORDER_FIELD_TO_HEADER["title"]: title,
            ORDER_FIELD_TO_HEADER["client"]: client,
            ORDER_FIELD_TO_HEADER["responsible"]: responsible,
            ORDER_FIELD_TO_HEADER["order_amount"]: amount,
            ORDER_FIELD_TO_HEADER["materials_amount"]: materials_amount,
            ORDER_FIELD_TO_HEADER["due_date"]: due_date.isoformat(),
            ORDER_FIELD_TO_HEADER["progress_percent"]: max(0, min(100, int(progress_percent))),
            ORDER_FIELD_TO_HEADER["story_points"]: max(0, int(story_points)),
        }
        if self._enabled and self.orders_sheet is not None:
            await asyncio.to_thread(self.orders_sheet.append_row, [order[h] for h in ORDER_HEADERS], "USER_ENTERED")
        else:
            self._orders_mem.append(order)
        rows_total = (
            len(await asyncio.to_thread(self.orders_sheet.get_all_values)) if self._enabled and self.orders_sheet is not None else len(self._orders_mem) + 1
        )
        return self._normalize_order_row(order, rows_total)

    async def list_active_orders(self) -> list[dict[str, Any]]:
        if self._enabled and self.orders_sheet is not None:
            rows = await asyncio.to_thread(self.orders_sheet.get_all_records)
        else:
            rows = list(self._orders_mem)
        normalized: list[dict[str, Any]] = []
        for idx, row in enumerate(rows, start=2):
            item = self._normalize_order_row(row, idx)
            if item["title"]:
                normalized.append(item)
        return normalized

    async def update_order_progress(self, order_id: str, progress_percent: int, status: str | None = None) -> bool:
        return await self.update_order_fields(order_id, progress_percent=max(0, min(100, int(progress_percent))))

    async def update_order_fields(
        self,
        order_id: str,
        *,
        title: str | None = None,
        client: str | None = None,
        responsible: str | None = None,
        amount: float | None = None,
        materials_amount: float | None = None,
        progress_percent: int | None = None,
        story_points: int | None = None,
        due_date: datetime | None = None,
        status: str | None = None,
    ) -> bool:
        row_idx = self._row_index_from_order_id(order_id)
        if row_idx is None:
            return False
        if self._enabled and self.orders_sheet is not None:
            updates: list[tuple[str, Any]] = []
            if title is not None:
                updates.append((ORDER_FIELD_TO_HEADER["title"], title))
            if client is not None:
                updates.append((ORDER_FIELD_TO_HEADER["client"], client))
            if responsible is not None:
                updates.append((ORDER_FIELD_TO_HEADER["responsible"], responsible))
            if amount is not None:
                updates.append((ORDER_FIELD_TO_HEADER["order_amount"], amount))
            if materials_amount is not None:
                updates.append((ORDER_FIELD_TO_HEADER["materials_amount"], materials_amount))
            if progress_percent is not None:
                updates.append((ORDER_FIELD_TO_HEADER["progress_percent"], max(0, min(100, int(progress_percent)))))
            if story_points is not None:
                updates.append((ORDER_FIELD_TO_HEADER["story_points"], max(0, int(story_points))))
            if due_date is not None:
                updates.append((ORDER_FIELD_TO_HEADER["due_date"], due_date.isoformat()))
            if status is not None and status.lower() in {"closed", "done", "cancelled"}:
                updates.append((ORDER_FIELD_TO_HEADER["title"], ""))
            for header, value in updates:
                col_idx = ORDER_HEADERS.index(header) + 1
                cell = rowcol_to_a1(row_idx, col_idx)
                await asyncio.to_thread(self.orders_sheet.update, cell, [[value]], value_input_option="USER_ENTERED")
            return bool(updates)

        mem_idx = row_idx - 2
        if mem_idx < 0 or mem_idx >= len(self._orders_mem):
            return False
        row = self._orders_mem[mem_idx]
        if title is not None:
            row[ORDER_FIELD_TO_HEADER["title"]] = title
        if client is not None:
            row[ORDER_FIELD_TO_HEADER["client"]] = client
        if responsible is not None:
            row[ORDER_FIELD_TO_HEADER["responsible"]] = responsible
        if amount is not None:
            row[ORDER_FIELD_TO_HEADER["order_amount"]] = amount
        if materials_amount is not None:
            row[ORDER_FIELD_TO_HEADER["materials_amount"]] = materials_amount
        if progress_percent is not None:
            row[ORDER_FIELD_TO_HEADER["progress_percent"]] = max(0, min(100, int(progress_percent)))
        if story_points is not None:
            row[ORDER_FIELD_TO_HEADER["story_points"]] = max(0, int(story_points))
        if due_date is not None:
            row[ORDER_FIELD_TO_HEADER["due_date"]] = due_date.isoformat()
        if status is not None and status.lower() in {"closed", "done", "cancelled"}:
            row[ORDER_FIELD_TO_HEADER["title"]] = ""
        return True

    async def close_order(self, order_id: str) -> bool:
        return await self.update_order_fields(order_id, status="closed")

    async def add_todo(
        self,
        from_user_id: int,
        to_user_id: int,
        text: str,
        due_at: datetime | None = None,
        priority: str = "normal",
    ) -> dict[str, Any]:
        todo = {
            "todo_id": str(uuid4())[:8],
            "from_user_id": from_user_id,
            "to_user_id": to_user_id,
            "text": text,
            "due_at": due_at.isoformat() if due_at else "",
            "priority": priority,
            "status": "open",
            "last_reminded_at": "",
        }
        if self._enabled and self.todos_sheet is not None:
            await asyncio.to_thread(self.todos_sheet.append_row, [todo[h] for h in TODO_HEADERS], "USER_ENTERED")
        else:
            self._todos_mem.append(todo)
        return todo

    async def list_open_todos(self, to_user_id: int | None = None) -> list[dict[str, Any]]:
        if self._enabled and self.todos_sheet is not None:
            rows = await asyncio.to_thread(self.todos_sheet.get_all_records)
        else:
            rows = list(self._todos_mem)
        filtered = [row for row in rows if str(row.get("status", "")).lower() not in {"done", "cancelled"}]
        if to_user_id is None:
            return filtered
        return [row for row in filtered if int(row.get("to_user_id", 0)) == to_user_id]

    async def mark_todo_reminded(self, todo_id: str) -> bool:
        if self._enabled and self.todos_sheet is not None:
            rows = await asyncio.to_thread(self.todos_sheet.get_all_records)
        else:
            rows = self._todos_mem
        for idx, row in enumerate(rows, start=2):
            if str(row.get("todo_id")) == todo_id:
                stamp = datetime.now(timezone.utc).isoformat()
                row["last_reminded_at"] = stamp
                if not (self._enabled and self.todos_sheet is not None):
                    return True
                col_idx = TODO_HEADERS.index("last_reminded_at") + 1
                cell = rowcol_to_a1(idx, col_idx)
                await asyncio.to_thread(self.todos_sheet.update, cell, [[stamp]], value_input_option="USER_ENTERED")
                return True
        return False
