from __future__ import annotations

import pytest

from bot.models.message_flow import MessageEnvelope, ModuleName, ModuleResponse, RouteDecision
from bot.services.orchestrator import MessageOrchestrator


class FakeDecisionMaker:
    def __init__(self, module: ModuleName) -> None:
        self.module = module

    async def decide(self, envelope: MessageEnvelope, *, memory_context: str = "", recent_chat=None) -> RouteDecision:  # noqa: ARG002, ANN001
        return RouteDecision(module=self.module, confidence=1.0)


class FakeMemory:
    def __init__(self) -> None:
        self.rows: list[tuple[str, int, int, str]] = []

    async def remember(self, role: str, user_id: int, chat_id: int, text: str) -> None:
        self.rows.append((role, user_id, chat_id, text))

    async def search_context(self, query: str, user_id=None, limit: int = 3, chat_id=None, fallback_user_ids=None):  # noqa: ANN001
        return "память: пользователь любит аккуратность"

    async def get_recent_chat_messages(self, chat_id: int, limit: int = 10):  # noqa: ARG002
        return [{"role": "user", "content": "старый контекст"}][:limit]


class FakeModule:
    def __init__(self, response: ModuleResponse) -> None:
        self.response = response
        self.calls = 0

    async def handle(self, request):  # noqa: ANN001
        self.calls += 1
        return self.response


@pytest.mark.asyncio
async def test_orchestrator_dispatches_and_persists() -> None:
    memory = FakeMemory()
    secretary = FakeModule(ModuleResponse(module=ModuleName.SECRETARY, text="Секретарь ответил"))
    generation = FakeModule(ModuleResponse(module=ModuleName.GENERATION, text="Готово", image_url="http://img"))
    chat = FakeModule(ModuleResponse(module=ModuleName.CHAT, text="Чат ответил"))
    orchestrator = MessageOrchestrator(FakeDecisionMaker(ModuleName.GENERATION), secretary, generation, chat, memory)
    result = await orchestrator.process(MessageEnvelope(user_id=7, chat_id=8, text="сделай картинку"))
    assert result.module == ModuleName.GENERATION
    assert result.image_url == "http://img"
    assert generation.calls == 1
    assert memory.rows[0] == ("user", 7, 8, "сделай картинку")
    assert memory.rows[1] == ("assistant", 7, 8, "Готово")


@pytest.mark.asyncio
async def test_orchestrator_fallbacks_to_chat_when_secretary_empty() -> None:
    memory = FakeMemory()
    secretary = FakeModule(ModuleResponse(module=ModuleName.SECRETARY, text=""))
    generation = FakeModule(ModuleResponse(module=ModuleName.GENERATION, text="img"))
    chat = FakeModule(ModuleResponse(module=ModuleName.CHAT, text="Чатовый fallback"))
    orchestrator = MessageOrchestrator(FakeDecisionMaker(ModuleName.SECRETARY), secretary, generation, chat, memory)
    result = await orchestrator.process(MessageEnvelope(user_id=1, chat_id=2, text="непонятный запрос"))
    assert result.module == ModuleName.CHAT
    assert secretary.calls == 1
    assert chat.calls == 1
