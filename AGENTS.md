# AGENTS.md — legal-agent-harness

Правила для агентов, работающих в этом репозитории.

## Назначение

Калибровка правовых агентов на экспертно-проверенных кейсах. Harness
детерминирован, работает офлайн, только стандартная библиотека.

## Инварианты (не нарушать)

1. **Только stdlib.** Никаких сторонних runtime-зависимостей. `pytest` —
   только для тестов (`[project.optional-dependencies].test`).
2. **Никакой сети.** Адаптеры не делают сетевых запросов. `local-process`
   запускает только переданную пользователем локальную команду, с таймаутом
   и лимитом размера вывода.
3. **Эксперт обязателен.** `final.needs_expert_approval` всегда `true`.
   `approved` — автоматическое совпадение, а не юридическое одобрение.
4. **Точная валидация.** `source` и `expected` проверяются на точный набор
   полей; лишние/пропущенные поля — ошибка.
5. **Редакция секретов.** Любые события проходят через `Redactor`; токены,
   email, ключи `token`/`secret`/`api_key`/`password` вырезаются.
6. **Ограниченные повторы.** `max_rounds` по умолчанию `3`; цикл обязан
   завершаться.

## Структура

- `src/legal_agent_harness/core.py` — валидация, события, `run_case`, редакция.
- `src/legal_agent_harness/adapters.py` — `ReplayAdapter`, `LocalProcessAdapter`.
- `scripts/run_harness.py` — CLI (`validate-cases`, `run`, `replay`, `report`).
- `schemas/calibration_case.schema.json` — JSON Schema кейса.
- `examples/` — образцы кейсов и ответов.
- `tests/test_harness.py` — модульные тесты (без сети).

## Соглашения

- Язык интерфейса, help и сообщений CLI — русский.
- Публичный API реэкспортируется из `legal_agent_harness/__init__.py`.
- Изменения — минимальным диффом, с сохранением существующего стиля.
- После правок: `python -m pytest -q tests/test_harness.py`.

## Запрещено

- Коммитить/пушить без явного запроса.
- Логировать или сохранять секреты и персональные данные.
- Добавлять сетевые вызовы, телеметрию или фоновые процессы.

## Цели публикации

- Hugging Face: `lawful-good-project/legal-agent-calibrations`
- Курс: `visualcomments/ip-law-course`
