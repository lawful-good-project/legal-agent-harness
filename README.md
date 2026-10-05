# legal-agent-harness

Минимальный рабочий harness для **калибровки правовых агентов** на наборе
экспертно-проверенных кейсов. Только стандартная библиотека Python, без сети.

Harness задаёт жёсткий формат кейса (источник + ожидаемое извлечение), прогоняет
агента через ограниченное число раундов повтора, фиксирует события жизненного
цикла и **всегда требует экспертного одобрения** финального результата.

## Возможности

- Строгая валидация кейсов: точный набор полей `source` и `expected`.
- События прогона: `task` → `agent_answer` → `validator_feedback` →
  `expert_review` → `final`.
- Ограниченное число раундов повтора (по умолчанию `3`).
- `final.needs_expert_approval` всегда `true` — автоматического одобрения нет.
- Редакция секретов: токены, email и ключи вида `token`/`secret`/`api_key`
  вырезаются из событий.
- Два адаптера: `replay` (заготовленные JSON-ответы) и `local-process`
  (только локальный процесс, с таймаутом и лимитом размера вывода).
  Скрытых сетевых вызовов нет.

## Структура

```
README.md
AGENTS.md
pyproject.toml
schemas/calibration_case.schema.json
src/legal_agent_harness/__init__.py
src/legal_agent_harness/core.py
src/legal_agent_harness/adapters.py
scripts/run_harness.py
examples/cases.jsonl
examples/responses.jsonl
tests/test_harness.py
.opencode/skills/legal-agent-calibration-harness/SKILL.md
```

## Формат кейса

Один JSON-объект на строку (JSONL). Обязательные поля:

```json
{
  "case_id": "case-001",
  "source": {
    "authority": "ВОИС",
    "jurisdiction": "RU",
    "doc_type": "statute",
    "identifier": "ГК РФ ст. 1259",
    "coordinate": "п. 1"
  },
  "expected": {
    "norm_id": "GK-RF-1259",
    "predicate": "предоставляет_охрану",
    "subject": "произведения науки",
    "object": "авторское право",
    "effective_from": "2008-01-01",
    "effective_to": null,
    "confidence": 0.9
  }
}
```

`schema` (JSON Schema, Draft 07): `schemas/calibration_case.schema.json`.
Поля `effective_from` / `effective_to` необязательны; `confidence` — число
в диапазоне `[0, 1]`.

## Формат ответа агента

Адаптер возвращает JSON-объект с полем `expected` (и опциональным `status`):

```json
{"status": "ok", "expected": { "norm_id": "...", "...": "..." }}
```

Сравнение с ожиданием кейса — точное по всем полям `expected`.

## Установка и запуск

```bash
pip install -e ".[test]"
```

Без установки:

```bash
python scripts/run_harness.py validate-cases examples/cases.jsonl
python scripts/run_harness.py replay \
  --cases examples/cases.jsonl --responses examples/responses.jsonl
python scripts/run_harness.py run \
  --cases examples/cases.jsonl --command "python my_agent.py" \
  --timeout 10 --max-output-bytes 200000
python scripts/run_harness.py report out.json
```

### Коды возврата CLI

| Код | Значение |
|-----|----------|
| `0` | успех |
| `1` | ошибка (валидация/адаптер/файл) |
| `2` | ошибка аргументов командной строки |

## Результат прогона

`run`/`replay` печатают JSON-массив результатов. Каждый результат:

- `case_id`, `status` (`approved` | `needs_expert_approval`);
- `rounds` — фактическое число раундов;
- `approved` — автоматическое совпадение (не экспертное одобрение);
- `needs_expert_approval` — всегда `true`;
- `events` — очищенные от секретов события прогона.

## Тесты

```bash
python -m pytest -q tests/test_harness.py
```

Тесты не используют сеть.

## Целевые репозитории

- Hugging Face (цель публикации набора): **`lawful-good-project/legal-agent-calibrations`**
- Курс: **`visualcomments/ip-law-course`**

## Ограничения и этика

- Harness не выносит правовых решений: он калибрует извлечение.
- Любой финал требует проверки человеком-экспертом.
- Агент подключается только через явный локальный процесс или replay;
  сетевого доступа harness не предоставляет.
