from __future__ import annotations

import logging

from bot.models.message_flow import MessageEnvelope, ModuleName, ModuleRequest, ModuleResponse
from bot.services.decision_maker import DecisionMakerService
from bot.services.memory import MemPalaceService
from bot.services.modules import ChatModule, GenerationModule, SecretaryModule

logger = logging.getLogger(__name__)


class MessageOrchestrator:
    def __init__(
        self,
        decision_maker: DecisionMakerService,
        secretary: SecretaryModule,
        generation: GenerationModule,
        chat: ChatModule,
        memory: MemPalaceService,
    ) -> None:
        self.decision_maker = decision_maker
        self.secretary = secretary
        self.generation = generation
        self.chat = chat
        self.memory = memory

    async def _dispatch(self, request: ModuleRequest) -> ModuleResponse:
        if request.route.module == ModuleName.SECRETARY:
            response = await self.secretary.handle(request)
            if response.text:
                return response
            # Safe fallback if secretary could not parse the request.
            fallback_request = ModuleRequest(
                envelope=request.envelope,
                route=request.route,
                memory_context=request.memory_context,
                recent_chat=request.recent_chat,
            )
            return await self.chat.handle(fallback_request)
        if request.route.module == ModuleName.GENERATION:
            return await self.generation.handle(request)
        return await self.chat.handle(request)

    async def process(self, envelope: MessageEnvelope) -> ModuleResponse:
        user_text = envelope.text.strip()
        await self.memory.remember("user", envelope.user_id, envelope.chat_id, user_text)
        memory_context = await self.memory.search_context(
            user_text,
            user_id=envelope.user_id,
            chat_id=envelope.chat_id,
            fallback_user_ids=[envelope.user_id],
            limit=5,
        )
        recent_chat = await self.memory.get_recent_chat_messages(chat_id=envelope.chat_id, limit=10)
        decision = await self.decision_maker.decide(
            envelope,
            memory_context=memory_context,
            recent_chat=recent_chat,
        )
        request = ModuleRequest(
            envelope=envelope,
            route=decision,
            memory_context=memory_context,
            recent_chat=recent_chat,
        )
        response = await self._dispatch(request)
        await self.memory.remember("assistant", envelope.user_id, envelope.chat_id, response.text)
        logger.info(
            "orchestrator_done chat_id=%s user_id=%s module=%s image=%s fallback=%s",
            envelope.chat_id,
            envelope.user_id,
            response.module.value,
            bool(response.image_url),
            decision.fallback_used,
        )
        return response
