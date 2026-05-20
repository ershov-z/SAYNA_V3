from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.services.reminders import ReminderService


class FakeSheets:
    def __init__(self, todos: list[dict[str, object]]) -> None:
        self.todos = todos
        self.mark_calls: list[str] = []

    async def list_open_todos(self) -> list[dict[str, object]]:
        return self.todos

    async def mark_todo_reminded(self, todo_id: str) -> bool:
        self.mark_calls.append(todo_id)
        stamp = datetime.now(timezone.utc).isoformat()
        for todo in self.todos:
            if str(todo.get("todo_id")) == todo_id:
                todo["last_reminded_at"] = stamp
        return True


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, user_id: int, text: str) -> None:
        self.messages.append((user_id, text))


@pytest.mark.asyncio
async def test_send_todo_reminders_sends_initial_dm_once() -> None:
    due_at = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    sheets = FakeSheets(
        [
            {
                "todo_id": "todo-1",
                "from_user_id": 1,
                "to_user_id": 2,
                "text": "Проверить смету",
                "due_at": due_at,
                "last_reminded_at": "",
            }
        ]
    )
    bot = FakeBot()
    service = ReminderService(sheets, bot)

    await service.send_todo_reminders()

    assert len(bot.messages) == 1
    assert bot.messages[0][0] == 2
    assert "Новая просьба" in bot.messages[0][1]
    assert sheets.mark_calls == ["todo-1"]


@pytest.mark.asyncio
async def test_send_todo_reminders_sends_followup_10_minutes_before_deadline_once() -> None:
    due_at_dt = datetime.now(timezone.utc) + timedelta(minutes=9)
    sheets = FakeSheets(
        [
            {
                "todo_id": "todo-2",
                "from_user_id": 1,
                "to_user_id": 2,
                "text": "Отправить фото",
                "due_at": due_at_dt.isoformat(),
                "last_reminded_at": (due_at_dt - timedelta(hours=1)).isoformat(),
            }
        ]
    )
    bot = FakeBot()
    service = ReminderService(sheets, bot)

    await service.send_todo_reminders()
    await service.send_todo_reminders()

    assert len(bot.messages) == 1
    assert "Получилось выполнить?" in bot.messages[0][1]
    assert sheets.mark_calls == ["todo-2"]
