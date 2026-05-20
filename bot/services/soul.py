from __future__ import annotations

import html
import re
from pathlib import Path

from bot.config import Settings


class SoulService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.persona = Path("bot/prompts/persona.md").read_text(encoding="utf-8")

    def module_style_prompt(self, module: str) -> str:
        return (
            "Соблюдай характер Сайны и говори естественно, по-человечески. "
            f"Активный модуль: {module}. "
            "Будь фактичной, не выдумывай данные."
        )

    def finalize_reply(self, raw_reply: str) -> str:
        cleaned = self._strip_reply_prefixes(raw_reply)
        trimmed = self._trim_reply(cleaned)
        return self._render_for_telegram_html(trimmed)

    def _trim_reply(self, reply: str) -> str:
        max_chars = self.settings.max_reply_chars
        if len(reply) <= max_chars:
            return reply.strip()
        cutoff = reply.rfind("\n", max(0, max_chars - 300), max_chars)
        if cutoff == -1:
            cutoff = reply.rfind(". ", max(0, max_chars - 300), max_chars)
        if cutoff == -1:
            cutoff = max_chars
        return reply[:cutoff].rstrip()

    @staticmethod
    def _strip_reply_prefixes(reply: str) -> str:
        cleaned = reply.strip()
        cleaned = re.sub(
            r"^\s*(?:\[[^\]]+\]\s*)?(?:assistant|ассистент)\s*:\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"^\s*\[[^\]]+\]\s*", "", cleaned)
        return cleaned.strip()

    @staticmethod
    def _render_for_telegram_html(reply: str) -> str:
        allowed_tags = ("b", "strong", "i", "em", "u", "s", "code", "pre")
        placeholders: dict[str, str] = {}
        tagged = reply
        for idx, tag in enumerate(allowed_tags):
            open_tag = f"<{tag}>"
            close_tag = f"</{tag}>"
            open_token = f"__TG_OPEN_{idx}__"
            close_token = f"__TG_CLOSE_{idx}__"
            tagged = tagged.replace(open_tag, open_token).replace(close_tag, close_token)
            placeholders[open_token] = open_tag
            placeholders[close_token] = close_tag

        escaped = html.escape(tagged, quote=False)
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped, flags=re.DOTALL)
        escaped = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", escaped)
        for token, tag in placeholders.items():
            escaped = escaped.replace(token, tag)
        return escaped
