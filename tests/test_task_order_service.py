from __future__ import annotations

from datetime import datetime

import pytest

from bot.services.task_order_service import TaskOrderService


class FakeSheets:
    def __init__(self) -> None:
        self.list_active_calls = 0
        self.closed_orders: list[str] = []
        self.progress_updates: list[tuple[str, int, str | None]] = []
        self.field_updates: list[tuple[str, dict[str, object]]] = []
        self.added_orders: list[dict[str, object]] = []
        self.todo_reminded: list[str] = []
        self.added_todos: list[dict[str, object]] = []
        self.orders = [
            {
                "order_id": "ab12cd34",
                "title": "Стол",
                "client": "Иван",
                "amount": 25000,
                "due_date": datetime(2026, 6, 15).isoformat(),
                "status": "active",
                "progress_percent": 40,
            },
            {
                "order_id": "cd34ef56",
                "title": "Шлем",
                "client": "Наташа",
                "amount": 12000,
                "due_date": datetime(2026, 6, 20).isoformat(),
                "status": "active",
                "progress_percent": 10,
            },
        ]

    async def add_order(
        self,
        title,
        client,
        amount,
        owner_user_id,
        due_date,
        *,
        responsible="",
        materials_amount=0.0,
        progress_percent=0,
        story_points=0,
    ):
        payload = {
            "order_id": "ab12cd34",
            "title": title,
            "client": client,
            "responsible": responsible,
            "amount": amount,
            "materials_amount": materials_amount,
            "owner_user_id": owner_user_id,
            "due_date": due_date.isoformat(),
            "status": "active",
            "progress_percent": progress_percent,
            "story_points": story_points,
        }
        self.added_orders.append(payload)
        return payload

    async def update_order_progress(self, order_id, progress_percent, status=None):
        self.progress_updates.append((order_id, progress_percent, status))
        return order_id in {"ab12cd34", "cd34ef56"}

    async def close_order(self, order_id):
        if order_id not in {"ab12cd34", "cd34ef56"}:
            return False
        self.closed_orders.append(order_id)
        return True

    async def update_order_fields(
        self,
        order_id,
        *,
        title=None,
        client=None,
        responsible=None,
        amount=None,
        materials_amount=None,
        progress_percent=None,
        story_points=None,
        due_date=None,
        status=None,
    ):
        if order_id not in {"ab12cd34", "cd34ef56"}:
            return False
        payload = {
            "title": title,
            "client": client,
            "responsible": responsible,
            "amount": amount,
            "materials_amount": materials_amount,
            "progress_percent": progress_percent,
            "story_points": story_points,
            "due_date": due_date.isoformat() if due_date else None,
            "status": status,
        }
        self.field_updates.append((order_id, payload))
        return True

    async def add_todo(self, from_user_id, to_user_id, text, due_at=None, priority="normal"):
        payload = {
            "todo_id": "todo1234",
            "to_user_id": to_user_id,
            "from_user_id": from_user_id,
            "text": text,
            "due_at": due_at.isoformat() if due_at else "",
        }
        self.added_todos.append(payload)
        return payload

    async def list_active_orders(self):
        self.list_active_calls += 1
        return [dict(item) for item in self.orders]

    async def list_open_todos(self):
        return [{"todo_id": "todo1234", "to_user_id": 2, "text": "помыть посуду"}]

    async def mark_todo_reminded(self, todo_id):
        self.todo_reminded.append(todo_id)
        return True


class FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str]] = []

    async def send_message(self, user_id: int, text: str) -> None:
        self.sent_messages.append((user_id, text))


class FakeDigest:
    async def build_digest(self, *, window_hours=24, trigger="manual"):  # noqa: ANN001
        class Result:
            text = "Собрала дайджест за сутки."

        return Result()


class FakeLLM:
    async def complete(self, messages, max_tokens=100000, timeout_seconds=None):  # noqa: ANN001
        text = str(messages[-1]["content"]).lower()
        if "удали первый заказ" in text and "палка из гачикуты" in text:
            return (
                '{"action_type":"mixed","confidence":0.87,"ambiguity":"",'
                '"preview":"Нашла два релевантных заказа. Подтверди удаление.",'
                '"operations":['
                '{"order_id":"ab12cd34","action":"close_order","value":""},'
                '{"order_id":"cd34ef56","action":"set_title","value":"Палка из гачикуты"}'
                "]}"
            )
        if "обнови заказ стол" in text and "21000" in text:
            return (
                '{"action_type":"update","confidence":0.95,"ambiguity":"",'
                '"preview":"Нашла изменения по заказу Стол.",'
                '"operations":['
                '{"order_id":"ab12cd34","action":"set_amount","value":"21000"},'
                '{"order_id":"ab12cd34","action":"set_due_date","value":"2026-06-25"},'
                '{"order_id":"ab12cd34","action":"set_title","value":"Стол дубовый"}'
                "]}"
            )
        if "у второго" in text and "клиент" in text:
            return (
                '{"action_type":"update","confidence":0.9,"ambiguity":"",'
                '"preview":"Поняла изменение по второму заказу.",'
                '"operations":[{"order_id":"cd34ef56","action":"set_client","value":"Олег"}]}'
            )
        if "первый заказ - ответственный я, захар" in text and "второй заказ - ответственная софа" in text:
            return (
                '{"action_type":"update","confidence":0.94,"ambiguity":"",'
                '"preview":"Обновляю ответственных и прогресс.",'
                '"operations":['
                '{"order_id":"ab12cd34","action":"set_client","value":"Захар"},'
                '{"order_id":"cd34ef56","action":"set_client","value":"Софа"},'
                '{"order_id":"cd34ef56","action":"set_progress","value":"10"}'
                "]}"
            )
        if "стол" in text and "ответственный - захар" in text and "стоимость - 12к" in text:
            return (
                '{"action_type":"update","confidence":0.92,"ambiguity":"",'
                '"preview":"Обновляю заказ Стол.",'
                '"operations":['
                '{"order_id":"ab12cd34","action":"set_client","value":"Захар"},'
                '{"order_id":"ab12cd34","action":"set_amount","value":"12000"}'
                "]}"
            )
        if "отредактируй заказ" in text and "палка из гачиакуты" in text and "стоимость материалов - 4000" in text:
            return (
                '{"action_type":"update","confidence":0.95,"ambiguity":"",'
                '"preview":"Обновляю поля заказа Палка из Гачиакуты.",'
                '"operations":['
                '{"order_id":"ab12cd34","action":"set_client","value":"я"},'
                '{"order_id":"ab12cd34","action":"set_amount","value":"12000"},'
                '{"order_id":"ab12cd34","action":"set_materials_amount","value":"4000"}'
                "]}"
            )
        return '{"preview":"Не распознано","operations":[]}'


@pytest.mark.asyncio
async def test_create_order_from_text():
    service = TaskOrderService(FakeSheets())
    res = await service.try_handle_command(
        "заказ Стол дубовый сумма 25000 дедлайн 2026-06-15 клиент Иван",
        from_user_id=1,
    )
    assert res.handled is True
    assert "Записала заказ" in res.text


@pytest.mark.asyncio
async def test_create_order_from_text_with_colon_after_order_keyword():
    service = TaskOrderService(FakeSheets())
    res = await service.try_handle_command(
        "Заказ: Похититель пламени ХСР сумма 60000 дедлайн 2026-08-03 клиент Джамиль",
        from_user_id=1,
    )
    assert res.handled is True
    assert "Записала заказ" in res.text


@pytest.mark.asyncio
async def test_create_order_from_flexible_text():
    service = TaskOrderService(FakeSheets())
    res = await service.try_handle_command(
        "взяли заказ на Плащ Гатсу цена 12000 дедлайн 10 июня клиент некошарк",
        from_user_id=1,
    )
    assert res.handled is True
    assert "Заказ добавлен" in res.text


@pytest.mark.asyncio
async def test_create_order_from_direct_address_with_colon_block():
    service = TaskOrderService(FakeSheets())
    res = await service.try_handle_command(
        "Сайна, Заказ: Похититель пламени ХСР, сумма: 60000, дедлайн 2026-08-03, клиент Джамиль",
        from_user_id=1,
    )
    assert res.handled is True
    assert "заказ" in res.text.lower()
    assert "не поймала точное изменение" not in res.text.lower()


@pytest.mark.asyncio
async def test_duplicate_create_requires_choice_and_creates_new_on_new_answer():
    sheets = FakeSheets()
    service = TaskOrderService(sheets)
    first = await service.try_handle_command(
        "заказ Стол сумма 25000 дедлайн 2026-06-15 клиент Иван",
        from_user_id=1,
    )
    assert first.handled is True
    assert "новый" in first.text.lower()
    assert "старый" in first.text.lower()
    assert sheets.added_orders == []

    second = await service.try_handle_command("новый", from_user_id=1)
    assert second.handled is True
    assert "создала новый заказ" in second.text.lower()
    assert len(sheets.added_orders) == 1


@pytest.mark.asyncio
async def test_duplicate_create_updates_existing_on_old_answer():
    sheets = FakeSheets()
    service = TaskOrderService(sheets)
    first = await service.try_handle_command(
        "заказ Стол сумма 21000 дедлайн 2026-06-25 клиент Иван",
        from_user_id=1,
    )
    assert first.handled is True
    assert "новый" in first.text.lower()
    assert "старый" in first.text.lower()

    second = await service.try_handle_command("старый", from_user_id=1)
    assert second.handled is True
    assert "обновила существующий заказ" in second.text.lower()
    assert sheets.field_updates
    order_id, payload = sheets.field_updates[-1]
    assert order_id == "ab12cd34"
    assert payload["amount"] == 21000


@pytest.mark.asyncio
async def test_update_progress_from_text():
    service = TaskOrderService(FakeSheets())
    res = await service.try_handle_command("прогресс ab12cd34 80 статус active", from_user_id=1)
    assert res.handled is True
    assert "Обновила прогресс" in res.text


@pytest.mark.asyncio
async def test_create_todo_from_text():
    service = TaskOrderService(FakeSheets())
    res = await service.try_handle_command("user2 должен помыть посуду до 2026-05-21", from_user_id=1)
    assert res.handled is True
    assert "Поручение создано" in res.text


@pytest.mark.asyncio
async def test_create_todo_from_natural_text_and_notify_target():
    sheets = FakeSheets()
    bot = FakeBot()
    service = TaskOrderService(sheets, bot=bot)
    res = await service.try_handle_command(
        "Попроси Катю отправить макет до 2026-05-21",
        from_user_id=752142337,
    )
    assert res.handled is True
    assert "Поручение создано" in res.text
    assert bot.sent_messages
    assert bot.sent_messages[0][0] == 495538754
    assert "Новая просьба" in bot.sent_messages[0][1]
    assert sheets.todo_reminded == ["todo1234"]


@pytest.mark.asyncio
async def test_create_todo_from_relative_time_phrase():
    sheets = FakeSheets()
    bot = FakeBot()
    service = TaskOrderService(sheets, bot=bot)
    res = await service.try_handle_command(
        "Сайна, через минуту напомни Кате закрыть у меня дверь",
        from_user_id=752142337,
    )
    assert res.handled is True
    assert "Поручение создано" in res.text
    assert sheets.added_todos
    assert sheets.added_todos[0]["to_user_id"] == 495538754
    assert sheets.added_todos[0]["due_at"] != ""


@pytest.mark.asyncio
async def test_list_orders_from_natural_query():
    sheets = FakeSheets()
    service = TaskOrderService(sheets)
    res = await service.try_handle_command("какие заказы сейчас активные?", from_user_id=1)
    assert res.handled is True
    assert "Сейчас в работе" in res.text
    assert "ID ab12cd34" in res.text
    assert sheets.list_active_calls == 1


@pytest.mark.asyncio
async def test_list_orders_from_what_are_these_orders_query():
    service = TaskOrderService(FakeSheets())
    res = await service.try_handle_command("а что это за заказы?", from_user_id=1)
    assert res.handled is True
    assert "Сейчас в работе" in res.text


@pytest.mark.asyncio
async def test_list_orders_from_what_we_have_query():
    service = TaskOrderService(FakeSheets())
    res = await service.try_handle_command("что у нас по заказам?", from_user_id=1)
    assert res.handled is True
    assert "Сейчас в работе" in res.text


@pytest.mark.asyncio
async def test_order_details_by_id_query():
    service = TaskOrderService(FakeSheets())
    res = await service.try_handle_command("что по заказу ab12cd34?", from_user_id=1)
    assert res.handled is True
    assert "картина такая" in res.text
    assert "Иван" in res.text


@pytest.mark.asyncio
async def test_order_details_by_client_name_query():
    service = TaskOrderService(FakeSheets())
    res = await service.try_handle_command("что по заказу для Ивана?", from_user_id=1)
    assert res.handled is True
    assert "картина такая" in res.text


@pytest.mark.asyncio
async def test_agentic_mutation_requires_confirmation_and_applies():
    sheets = FakeSheets()
    service = TaskOrderService(sheets, llm=FakeLLM())
    plan = await service.try_handle_command(
        "Удали первый заказ, у второго название - Палка из гачикуты",
        from_user_id=1,
    )
    assert plan.handled is True
    assert "подтверди" in plan.text.lower()
    assert any("Палка из гачикуты" in str(update[1].get("title", "")) for update in sheets.field_updates)
    assert sheets.closed_orders == []

    apply = await service.try_handle_command("Подтверждаю", from_user_id=1)
    assert apply.handled is True
    assert "удалила заказ ab12cd34" in apply.text
    assert sheets.closed_orders == ["ab12cd34"]


@pytest.mark.asyncio
async def test_agentic_mutation_updates_deadline_price_and_title():
    sheets = FakeSheets()
    service = TaskOrderService(sheets, llm=FakeLLM())
    result = await service.try_handle_command(
        "Обнови заказ стол: поставь дедлайн 2026-06-25 и прайс 21000, тайтл Стол дубовый",
        from_user_id=1,
    )
    assert result.handled is True
    assert "подтверди" not in result.text.lower()
    assert "обновила сумму заказа ab12cd34 на 21000" in result.text
    assert "обновила дедлайн заказа ab12cd34 на 2026-06-25" in result.text
    assert "обновила название заказа ab12cd34 на «Стол дубовый»" in result.text
    assert sheets.list_active_calls == 1


@pytest.mark.asyncio
async def test_contextual_order_reference_second_order():
    service = TaskOrderService(FakeSheets())
    first = await service.try_handle_command("покажи заказы", from_user_id=1)
    assert first.handled is True
    second = await service.try_handle_command("что по второму заказу?", from_user_id=1)
    assert second.handled is True
    assert "Шлем" in second.text


@pytest.mark.asyncio
async def test_order_mutation_prefetches_orders_snapshot():
    sheets = FakeSheets()
    service = TaskOrderService(sheets, llm=FakeLLM())
    result = await service.try_handle_command("обнови заказ стол прайс 21000", from_user_id=1)
    assert result.handled is True
    assert sheets.list_active_calls == 1


@pytest.mark.asyncio
async def test_human_multi_order_mutation_with_ordinals():
    sheets = FakeSheets()
    service = TaskOrderService(sheets, llm=FakeLLM())
    listed = await service.try_handle_command("Скинь список заказов", from_user_id=1)
    assert listed.handled is True

    result = await service.try_handle_command(
        "Первый заказ - ответственный я, Захар. Второй заказ - ответственная Софа. Так же там 10 процентов прогресса",
        from_user_id=1,
    )
    assert result.handled is True
    assert "обновила ответственного заказа ab12cd34 на «Захар»" in result.text
    assert "обновила ответственного заказа cd34ef56 на «Софа»" in result.text
    assert "обновила прогресс заказа cd34ef56 до 10%" in result.text


@pytest.mark.asyncio
async def test_human_block_mutation_keeps_previous_target():
    sheets = FakeSheets()
    service = TaskOrderService(sheets, llm=FakeLLM())
    result = await service.try_handle_command(
        "Стол\nОтветственный - Захар\nСтоимость - 12к",
        from_user_id=1,
    )
    assert result.handled is True
    assert "обновила ответственного заказа ab12cd34 на «Захар»" in result.text
    assert "обновила сумму заказа ab12cd34 на 12000" in result.text


@pytest.mark.asyncio
async def test_create_order_from_multiline_block_with_materials_and_responsible():
    service = TaskOrderService(FakeSheets())
    res = await service.try_handle_command(
        "Заведи заказ\n\n"
        "Палка из Гачиакуты\n"
        "Клиент - некошарк\n"
        "Ответственный - я\n"
        "Стоимость материалов - 4к\n"
        "Сумма заказа - 12к\n"
        "Дедлайн - 10 июня\n"
        "Готовность - 10 процентов",
        from_user_id=1,
    )
    assert res.handled is True
    assert "Заказ добавлен" in res.text


@pytest.mark.asyncio
async def test_edit_order_natural_block_uses_mutation_not_creation():
    sheets = FakeSheets()
    service = TaskOrderService(sheets, llm=FakeLLM())
    result = await service.try_handle_command(
        "Сайна, отредактируй заказ\n"
        "Палка из гачиакуты\n"
        "Ответственный - я\n"
        "Стоимость заказа - 12000\n"
        "Стоимость материалов - 4000",
        from_user_id=1,
        sender_display_name="Захар",
    )
    assert result.handled is True
    assert "не хватает данных" not in result.text.lower()
    assert "обновила ответственного заказа ab12cd34 на «Захар»" in result.text
    assert "обновила сумму заказа ab12cd34 на 12000" in result.text
    assert "обновила стоимость материалов заказа ab12cd34 на 4000" in result.text


@pytest.mark.asyncio
async def test_manual_digest_command_calls_digest_service():
    service = TaskOrderService(FakeSheets(), digest=FakeDigest())  # type: ignore[arg-type]
    result = await service.try_handle_command("/digest", from_user_id=1)
    assert result.handled is True
    assert "дайджест" in result.text.lower()
