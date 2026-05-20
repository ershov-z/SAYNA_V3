from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class Order:
    order_id: str
    title: str
    client: str
    responsible: str
    order_amount: float
    materials_amount: float
    due_date: datetime
    progress_percent: int
    story_points: int


@dataclass(slots=True)
class Todo:
    todo_id: str
    from_user_id: int
    to_user_id: int
    text: str
    due_at: datetime | None
    priority: str
    status: str
    last_reminded_at: datetime | None


@dataclass(slots=True)
class Event:
    event_id: str
    owner_user_id: int
    title: str
    at: datetime
    notes: str
    remind_before_min: int
