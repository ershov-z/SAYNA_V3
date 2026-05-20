from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ModuleName(str, Enum):
    SECRETARY = "secretary"
    GENERATION = "generation"
    CHAT = "chat"


@dataclass(slots=True)
class MessageEnvelope:
    user_id: int
    chat_id: int
    text: str
    user_display_name: str = ""
    images: list[str] = field(default_factory=list)
    is_group_chat: bool = False
    context_hint: str = ""
    reply_to_text: str = ""
    reply_to_user_id: int | None = None


@dataclass(slots=True)
class RouteDecision:
    module: ModuleName
    confidence: float
    reason: str = ""
    raw: str = ""
    fallback_used: bool = False


@dataclass(slots=True)
class ModuleRequest:
    envelope: MessageEnvelope
    route: RouteDecision
    memory_context: str = ""
    recent_chat: list[dict[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class ModuleResponse:
    module: ModuleName
    text: str = ""
    image_url: str = ""
