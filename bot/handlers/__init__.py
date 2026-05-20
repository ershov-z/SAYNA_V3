from aiogram import Router

from bot.handlers.group import setup_group_handlers
from bot.handlers.private import setup_private_handlers
from bot.services.container import ServiceContainer


def build_router(services: ServiceContainer) -> Router:
    root = Router(name="root")
    root.include_router(setup_private_handlers(services))
    root.include_router(setup_group_handlers(services))
    return root
