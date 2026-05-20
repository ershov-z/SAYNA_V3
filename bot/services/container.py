from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot

from bot.config import Settings
from bot.services.chad_ai import ChadAIClient
from bot.services.decision_maker import DecisionMakerService
from bot.services.dialogue import DialogueService
from bot.services.image_generation import ChadImageService
from bot.services.image_intent_scorer import ImageIntentScorer
from bot.services.image_prompt_service import ImagePromptService
from bot.services.intent_scorer import GroupIntentScorer
from bot.services.memory import MemPalaceService
from bot.services.modules import ChatModule, GenerationModule, SecretaryModule
from bot.services.orchestrator import MessageOrchestrator
from bot.services.sheets import GoogleSheetsService
from bot.services.soul import SoulService
from bot.services.startup_selftest import StartupSelfTestService
from bot.services.task_order_service import TaskOrderService


@dataclass(slots=True)
class ServiceContainer:
    settings: Settings
    bot: Bot
    llm: ChadAIClient
    soul: SoulService
    memory: MemPalaceService
    sheets: GoogleSheetsService
    decision_maker: DecisionMakerService
    task_order: TaskOrderService
    dialogue: DialogueService
    secretary_module: SecretaryModule
    generation_module: GenerationModule
    chat_module: ChatModule
    orchestrator: MessageOrchestrator
    image: ChadImageService
    image_intent: ImageIntentScorer
    intent_scorer: GroupIntentScorer
    startup_selftest: StartupSelfTestService


def build_services(settings: Settings, bot: Bot) -> ServiceContainer:
    llm = ChadAIClient(settings)
    soul = SoulService(settings)
    memory = MemPalaceService(settings, reranker=llm)
    sheets = GoogleSheetsService(settings)
    decision_maker = DecisionMakerService(settings, llm, soul)
    task_order = TaskOrderService(sheets, llm=llm, soul=soul, bot=bot)
    dialogue = DialogueService(settings, llm, memory, soul)
    image_prompt = ImagePromptService()
    image = ChadImageService(settings, prompt_service=image_prompt)
    image_intent = ImageIntentScorer(settings, llm)
    intent_scorer = GroupIntentScorer(settings, llm, memory)
    secretary_module = SecretaryModule(task_order, soul)
    generation_module = GenerationModule(image, soul)
    chat_module = ChatModule(dialogue)
    orchestrator = MessageOrchestrator(decision_maker, secretary_module, generation_module, chat_module, memory)
    startup_selftest = StartupSelfTestService(settings, dialogue, llm)
    return ServiceContainer(
        settings=settings,
        bot=bot,
        llm=llm,
        soul=soul,
        memory=memory,
        sheets=sheets,
        decision_maker=decision_maker,
        task_order=task_order,
        dialogue=dialogue,
        secretary_module=secretary_module,
        generation_module=generation_module,
        chat_module=chat_module,
        orchestrator=orchestrator,
        image=image,
        image_intent=image_intent,
        intent_scorer=intent_scorer,
        startup_selftest=startup_selftest,
    )
