---
name: legal-agent-calibration-harness
description: Калибровка правовых агентов извлечения на экспертно-проверенных кейсах. Использовать при работе с legal-agent-harness: валидации кейсов, прогонах (replay/local-process), отчётах, а также при расширении схемы или адаптеров.
---

# Навык: legal-agent-calibration-harness

## Когда применять

- Нужно проверить/запустить калибровочный набор правового агента.
- Нужно добавить кейсы, адаптер или поле в схему.
- Нужно понять формат событий и требования к экспертному одобрению.

## Быстрый старт

```bash
python -m pytest -q tests/test_harness.py
python scripts/run_harness.py validate-cases examples/cases.jsonl
python scripts/run_harness.py replay --cases examples/cases.jsonl \
  --responses examples/responses.jsonl
python scripts/run_harness.py report out.json
```

## Рабочий процесс

1. **Прочитать инварианты** в `AGENTS.md` (stdlib-only, без сети, эксперт
   обязателен, точная валидация, редакция секретов, bounded retry).
2. **Кейсы**: JSONL, обязательные поля `source`/`expected` — см.
   `schemas/calibration_case.schema.json`. Валидация — `validate_case`.
3. **Ответы**: JSON-объект с полем `expected`. Replay-адаптер возвращает
   заготовленный ответ без изменений.
4. **Прогон**: `run_case(case, adapter, max_rounds=3)`. События:
   `task → agent_answer → validator_feedback → expert_review → final`.
   `final.needs_expert_approval` всегда `true`.
5. **Адаптеры**: `replay` или `local-process` (локальный процесс, `timeout`
   и `max_output_bytes`). Сеть не использовать.
6. **Проверка**: `python -m pytest -q tests/test_harness.py`.

## Инварианты

- Только стандартная библиотека (pytest — только для тестов).
- Никаких сетевых вызовов; `local-process` — только локальная команда.
- Секреты/токены/email вырезаются (`Redactor`, плейсхолдер `[REDACTED]`).
- `approved` — автоматическое совпадение, не юридическое одобрение.
- Повторы ограничены (`max_rounds`, по умолчанию 3).

## Публикация

- Hugging Face: `lawful-good-project/legal-agent-calibrations`
- Курс: `visualcomments/ip-law-course`

Не коммитить и не пушить без явного указания.
