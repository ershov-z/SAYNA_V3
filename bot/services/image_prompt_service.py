from __future__ import annotations

from enum import StrEnum
import re

SAINA_APPEARANCE_PROMPT = """
Персонаж: Сайна, аниме-девушка ассистент-секретарь TENEBRIS COSPLAY WORKSHOP.

Внешность:
Сайна выглядит как молодая взрослая девушка 22-25 лет. У неё спокойная, умная и немного загадочная внешность. Лицо мягкое, аккуратное, с выразительными внимательными глазами. Взгляд живой, собранный, будто она одновременно слушает собеседника и держит в голове список задач. Выражение лица обычно спокойное, с лёгкой уверенной полуулыбкой.

Волосы:
Длинные волосы холодного оттенка: серебристо-белые, пепельно-голубые или светло-серые, с мягким голубым или фиолетовым градиентом на концах. Волосы гладкие, но не идеально пластиковые: есть живые пряди, лёгкая асимметрия, несколько прядей у лица. Причёска аккуратная, секретарская, но не строгая. Можно добавить маленькую заколку, ленту или минималистичный технологичный аксессуар.

Глаза:
Большие выразительные глаза холодного оттенка: голубые, серо-голубые или фиолетово-синие. В глазах лёгкий эффект цифрового свечения, но очень деликатный, без сильного киберпанка. Взгляд внимательный, заботливый, немного ироничный.

Телосложение:
Стройная взрослая фигура, естественные пропорции, изящная осанка. Она выглядит собранной, уверенной и аккуратной. Не детская, не чрезмерно сексуализированная, без гипертрофированных пропорций.

Одежда:
Стильный образ секретаря мастерской с лёгкой AI-эстетикой. Белая или светло-серая рубашка, тёмный жилет или укороченный жакет, аккуратная юбка-карандаш или строгие брюки. На шее тонкая лента, галстук или аккуратный бант. Цветовая палитра: белый, графитовый, тёмно-синий, серебристый, холодный голубой, немного фиолетового акцента.

Детали:
На ней может быть бейдж, маленький планшет, стилус, блокнот, чеклист заказов, сантиметровая лента, ключи от мастерской, небольшие аккуратные инструменты на поясе или рядом. Детали должны подчёркивать, что она секретарь косплейной мастерской: она следит за заказами, дедлайнами, материалами и расписанием.

Общее ощущение:
Сайна - не боевой андроид и не офисный NPC. Она выглядит как персонажный AI-ассистент TENEBRIS COSPLAY WORKSHOP, который стал лицом мастерской: умная, спокойная, немного язвительная, заботливая, организованная. В её образе должны сочетаться уют мастерской, порядок, технологичность и лёгкая загадочность.

Стиль изображения:
Чистая современная аниме-иллюстрация, аккуратный дизайн персонажа, мягкий свет, детализированность без перегруза, элегантный силуэт, читаемый дизайн, высокое качество иллюстрации, мягкие тени, деликатное футуристичное свечение интерфейсов, уютная атмосфера мастерской.

Важно:
Сохранять узнаваемость персонажа: холодные светлые волосы, голубо-фиолетовые акценты, спокойный внимательный взгляд, секретарско-мастерской образ, планшет или чеклист как ключевой аксессуар.
""".strip()


class ImagePromptService:
    class GenerationMode(StrEnum):
        NONE = "none"
        SIMPLE = "simple"
        SELF = "self"

    IMAGE_TRIGGER_PATTERNS = (
        r"\bсгенер\w*",
        r"\bнарис\w*",
        r"\bсоздай\s+(?:картин\w*|изображен\w*|арт|фото)\b",
        r"\bсделай\s+(?:картин\w*|арт|иллюстрац\w*|фото)\b",
        r"\bсделай\s+селфи\b",
        r"\bпокажи\s+как\s+ты\b",
        r"\bпокажи\s+себя\b",
        r"\bкартинк\w*\b",
        r"\bизображен\w*\b",
        r"\bарт\b",
        r"\bфотк\w*\b",
        r"\bаватар\w*\b",
        r"\billustration\b",
        r"\bimage\b",
    )
    SELF_DESCRIPTION_PATTERNS = (
        r"\bопиши\s+себя\b",
        r"\bпокажи\s+себя\b",
        r"\bнарисуй\s+себя\b",
        r"\bкак\s+ты\s+выглядишь\b",
        r"\bкак\s+выглядишь\b",
        r"\bтвоя\s+внешн\w*\b",
        r"\bтво[её]\s+лиц\w*\b",
        r"\bтвой\s+образ\b",
        r"\bпришли\s+(?:сво[её]|тво[её])\s+(?:фото|картин\w*|арт)\b",
        r"\bсво[ею]\s+фот\w*\b",
        r"\bфот\w*\s+себя\b",
        r"\bселфи\b",
    )
    SAINA_PATTERNS = (
        r"\bсайн\w*\b",
        r"\bsaina\b",
        r"\bбот\w*\b",
        r"\bассистент\w*\b",
        r"\bсекретар\w*\b",
        r"\bпокажи\s+себя\b",
        r"\bнарисуй\s+себя\b",
        r"\bкак\s+ты\s+выглядишь\b",
        r"\bсво[её]\s+селфи\b",
        r"\bсво[её]\s+фот\w*\b",
        r"\bфот\w*\s+себя\b",
        r"\bселфи\b",
    )
    SIMPLE_PREFIX_PATTERNS = (
        r"^\s*(?:ну\s+)?(?:пожалуйста[, ]+)?",
        r"^\s*(?:сайна|saina)[,:\-]?\s*",
        r"^\s*(?:сгенерируй|сгенерь|нарисуй|создай|сделай|пришли|скинь|покажи)\s+",
        r"^\s*(?:мне\s+)?(?:картин\w*|изображен\w*|арт|фото|фотк\w*|селфи)\s+",
    )
    SELF_PREFIX_PATTERNS = (
        r"^\s*(?:ну\s+)?(?:пожалуйста[, ]+)?",
        r"^\s*(?:сайна|saina)[,:\-]?\s*",
        r"^\s*(?:скинь|пришли|сделай|покажи|нарисуй|сгенерируй|сгенерь|создай)\s+",
        r"^\s*(?:мне\s+)?(?:(?:сво[её]|тво[её])\s+)?(?:селфи|фото|фотк\w*|картин\w*|изображен\w*|арт)\s*",
    )
    _SELF_CONTEXT_PREPOSITIONS = ("в ", "во ", "на ", "с ", "со ", "под ", "без ", "для ", "как ", "у ")

    @staticmethod
    def _clean_user_prompt(text: str) -> str:
        prompt = text.strip()
        prompt = re.sub(r"^\s*(сайна[,:\-]?\s*)", "", prompt, flags=re.IGNORECASE)
        return prompt.strip()

    @classmethod
    def _strip_prefixes(cls, text: str, patterns: tuple[str, ...]) -> str:
        value = text.strip()
        changed = True
        while changed and value:
            changed = False
            for pattern in patterns:
                updated = re.sub(pattern, "", value, count=1, flags=re.IGNORECASE).strip()
                if updated != value:
                    value = updated
                    changed = True
        return value

    def is_image_request(self, text: str) -> bool:
        lowered = text.lower().strip()
        if not lowered:
            return False
        return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in self.IMAGE_TRIGGER_PATTERNS) or self.is_self_description_request(lowered)

    def is_self_description_request(self, text: str) -> bool:
        lowered = text.lower().strip()
        return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in self.SELF_DESCRIPTION_PATTERNS)

    def is_saina_request(self, text: str) -> bool:
        lowered = text.lower().strip()
        return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in self.SAINA_PATTERNS)

    def classify_generation_mode(self, text: str) -> GenerationMode:
        raw = (text or "").strip()
        if not raw:
            return self.GenerationMode.NONE
        if self.is_self_description_request(raw) or self.is_saina_request(raw):
            return self.GenerationMode.SELF

        cleaned = self._clean_user_prompt(text)
        if not cleaned or not self.is_image_request(raw):
            return self.GenerationMode.NONE
        return self.GenerationMode.SIMPLE

    def build_simple_prompt(self, user_text: str) -> str:
        cleaned = self._clean_user_prompt(user_text)
        if not cleaned:
            return ""
        prompt = self._strip_prefixes(cleaned, self.SIMPLE_PREFIX_PATTERNS)
        prompt = re.sub(r"^[,.:;!?-]+\s*", "", prompt).strip()
        return prompt or cleaned

    def build_self_prompt(self, user_text: str) -> str:
        cleaned = self._clean_user_prompt(user_text)
        if not cleaned:
            return ""
        context = self._strip_prefixes(cleaned, self.SELF_PREFIX_PATTERNS)
        context = re.sub(r"^[,.:;!?-]+\s*", "", context).strip()
        context = re.sub(r"^(?:себя|себе)\b\s*", "", context, flags=re.IGNORECASE).strip()
        if not context:
            return "Селфи персонажа"
        lowered = context.lower()
        if lowered.startswith(self._SELF_CONTEXT_PREPOSITIONS):
            return f"Селфи персонажа {context}"
        return f"Селфи персонажа в {context}"

    def build_base_prompt(self, user_text: str) -> str:
        mode = self.classify_generation_mode(user_text)
        if mode is self.GenerationMode.SELF:
            return self.build_self_prompt(user_text)
        if mode is self.GenerationMode.SIMPLE:
            return self.build_simple_prompt(user_text)
        return ""

    def build_caption(self, user_text: str) -> str:
        cleaned = self._clean_user_prompt(user_text)
        if not cleaned:
            return "Готово."
        excerpt = cleaned.replace("\n", " ").strip()
        if len(excerpt) > 160:
            excerpt = excerpt[:157].rstrip() + "..."
        if self.classify_generation_mode(user_text) is self.GenerationMode.SELF:
            return f"Сгенерировала Сайну по запросу: {excerpt}"
        return f"Сгенерировала по запросу: {excerpt}"
