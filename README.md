# Workshop Telegram Assistant

Telegram-бот на `Python + aiogram` для 3 пользователей мастерской:
- персонажный ассистент в ЛС и группе;
- секретарь по заказам и дедлайнам;
- персональные и межпользовательские напоминания;
- память через `MemPalace`.

## 1) Быстрый старт

1. Установите Python 3.11+.
2. Создайте виртуальное окружение и установите зависимости:
   - `python -m venv .venv`
   - `.venv\Scripts\activate` (Windows)
   - `pip install -e .`
3. Скопируйте `.env.example` в `.env` и заполните значения.
4. Добавьте Google Service Account JSON в `credentials/google-service-account.json`.
5. Выдайте сервисному аккаунту доступ к Google Sheet.
6. Запустите бота:
   - `python -m bot.main`

## 2) Основные флоу

- Создание заказа:
  - `заказ Стол дубовый сумма 25000 дедлайн 2026-06-15 клиент Иван`
- Обновление прогресса:
  - `прогресс ab12cd34 65 статус active`
- Создание поручения:
  - `попроси Катю отправить макет до 2026-05-21`
  - `user2 должен помыть посуду до 2026-05-21`
- Просмотр списков:
  - `/orders`, `/todos` или `список заказов`, `список задач`

## 3) Access control

- Бот принимает сообщения только от `ALLOWED_USER_IDS`.
- В группах отвечает только в `ALLOWED_CHAT_IDS`.
- Посторонние пользователи/чаты silently игнорируются.

## 4) Smart participation в группе

Бот отвечает:
- когда его упоминают (`@username`) или отвечают на его сообщение;
- при триггерах по мастерской (`заказ`, `дедлайн`, `прогресс`, `напомни`, ...);
- в контексте обсуждения с ограниченной вероятностью (`GROUP_CONTEXT_PROBABILITY`).

Антифлуд: `GROUP_COOLDOWN_SECONDS`.

## 5) Scheduler

Запускаются регулярные задачи:
- ежедневный запрос прогресса по активным заказам;
- проверка риска дедлайнов каждые 3 часа;
- напоминания по задачам с интервалом `REMINDER_INTERVAL_MINUTES`;
  - первое сообщение уходит исполнителю в ЛС сразу после создания поручения;
  - за 10 минут до дедлайна бот отправляет follow-up: «получилось ли выполнить?»;
- `mempalace sweep` каждые 6 часов.

## 6) MemPalace

- MemPalace интегрирован напрямую в Python-код (без вызовов CLI).
- Данные хранятся локально в palace-директории:
  - `MEMPALACE_PALACE_DIR` (по умолчанию `data/mempalace-palace`)
- Wing-структура создаётся и пополняется в runtime автоматически:
  - общий wing: `<MEMPALACE_WING_PREFIX>_shared`
  - личный wing: `<MEMPALACE_WING_PREFIX>_user_<user_id>`
- Room выбирается автоматически по смыслу сообщения (`orders`, `tasks`, `materials`, `group_chat`, `private_dialogue`).
- Поиск контекста идёт сначала в общем wing, затем в личных.
- Опциональный LLM-rerank через Chad API для повышения релевантности:
  - `MEMORY_RERANK_ENABLED` (по умолчанию `false`)
  - `MEMORY_RERANK_MIN_CANDIDATES` (по умолчанию `4`)
  - `MEMORY_RERANK_CANDIDATE_LIMIT` (по умолчанию `8`)
  - `MEMORY_RERANK_FINAL_LIMIT` (по умолчанию `3`)
  - `MEMORY_RERANK_TIMEOUT_SECONDS` (по умолчанию `1.8`)

Установите пакет:
- `pip install mempalace`

Если `mempalace` не установлен, запуск бота завершится с явной ошибкой конфигурации.

## 7) Деплой на bothost.ru (через git)

Используется путь `git + venv + systemd`:
1. На сервере:
   - `git clone <repo_url> /opt/workshop-bot`
   - `cd /opt/workshop-bot`
2. Подготовка окружения:
   - `python3 -m venv .venv`
   - `. .venv/bin/activate`
   - `pip install -e .`
3. Конфиг:
   - `cp .env.example .env`
   - заполнить `.env`
   - загрузить `credentials/google-service-account.json`
4. Установка systemd-сервиса:
   - `sudo cp deploy/workshop-bot.service /etc/systemd/system/workshop-bot.service`
   - при необходимости скорректировать пути и `User/Group`
   - `sudo systemctl daemon-reload`
   - `sudo systemctl enable workshop-bot`
   - `sudo systemctl start workshop-bot`
5. Проверка:
   - `sudo systemctl status workshop-bot`
   - `journalctl -u workshop-bot -f`

Обновление после новых коммитов:
- `cd /opt/workshop-bot && git pull`
- `. .venv/bin/activate && pip install -e .`
- `sudo systemctl restart workshop-bot`

## 8) Приемочные проверки MVP

1. **Доступы**
   - посторонний пользователь не получает ответа;
   - разрешенный пользователь в ЛС получает ответ.
2. **Группа**
   - бот реагирует на `@mention`;
   - бот не спамит подряд (cooldown работает).
3. **Заказы**
   - заказ создается и отображается в `/orders`;
   - прогресс обновляется.
4. **Поручения**
   - задача от user1 к user2 создается;
   - user2 получает напоминание.
5. **Дедлайны**
   - при риске срыва приходит предупреждение.
6. **Память**
   - контекст диалога сохраняется в `data/mempalace-palace`;
   - при включенном `MEMORY_RERANK_ENABLED=true` ответы не должны заметно замедлиться.

## 9) Rollout checklist для Memory Rerank

1. На первом релизе оставить `MEMORY_RERANK_ENABLED=false`.
2. Убедиться, что бот стабильно отвечает в ЛС/группе без деградации latency.
3. Включить `MEMORY_RERANK_ENABLED=true` только для рабочего окружения после smoke-check.
4. Проверить целевые вопросы по профилям (`о Софе`, `обо мне`, `что знаешь о Кате`) и сравнить качество.
5. Если задержка выросла, уменьшить `MEMORY_RERANK_CANDIDATE_LIMIT` или `MEMORY_RERANK_TIMEOUT_SECONDS`.
