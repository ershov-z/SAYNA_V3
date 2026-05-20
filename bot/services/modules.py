from __future__ import annotations

from bot.models.message_flow import ModuleName, ModuleRequest, ModuleResponse
from bot.services.dialogue import DialogueService
from bot.services.image_generation import ChadImageService
from bot.services.soul import SoulService
from bot.services.task_order_service import TaskOrderService


class SecretaryModule:
    def __init__(self, task_order: TaskOrderService, soul: SoulService) -> None:
        self.task_order = task_order
        self.soul = soul

    async def handle(self, request: ModuleRequest) -> ModuleResponse:
        result = await self.task_order.try_handle_command(
            request.envelope.text,
            request.envelope.user_id,
            memory_context=request.memory_context,
            sender_display_name=request.envelope.user_display_name,
        )
        if not result.handled:
            return ModuleResponse(module=ModuleName.SECRETARY, text="")
        return ModuleResponse(module=ModuleName.SECRETARY, text=self.soul.finalize_reply(result.text))


class GenerationModule:
    def __init__(self, image: ChadImageService, soul: SoulService) -> None:
        self.image = image
        self.soul = soul

    async def handle(self, request: ModuleRequest) -> ModuleResponse:
        result = await self.image.try_generate(request.envelope.text)
        if result.handled and result.success:
            caption_source = result.caption or "Готово."
            if request.memory_context.strip():
                memory_hint = request.memory_context.splitlines()[-1][:180]
                caption_source = f"{caption_source}\nУчла контекст из памяти: {memory_hint}"
            caption = self.soul.finalize_reply(caption_source)
            return ModuleResponse(module=ModuleName.GENERATION, text=caption, image_url=result.image_url)
        if result.handled:
            return ModuleResponse(module=ModuleName.GENERATION, text=self.soul.finalize_reply(result.error_message))
        return ModuleResponse(
            module=ModuleName.GENERATION,
            text=self.soul.finalize_reply("Не вижу запроса на генерацию. Сформулируй, что нужно сгенерировать."),
        )


class ChatModule:
    def __init__(self, dialogue: DialogueService) -> None:
        self.dialogue = dialogue

    async def handle(self, request: ModuleRequest) -> ModuleResponse:
        reply = await self.dialogue.answer(
            user_id=request.envelope.user_id,
            chat_id=request.envelope.chat_id,
            user_text=request.envelope.text,
            images=request.envelope.images,
            context_hint=request.envelope.context_hint,
            is_group_chat=request.envelope.is_group_chat,
            reply_to_text=request.envelope.reply_to_text,
            reply_to_user_id=request.envelope.reply_to_user_id,
            prefetched_memory_context=request.memory_context,
            persist=False,
        )
        return ModuleResponse(module=ModuleName.CHAT, text=reply)
