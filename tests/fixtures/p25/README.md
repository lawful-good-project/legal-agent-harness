# P25 — схема и валидатор (эксперт: Александр Неверов)

Задание: один корректный кейс и две копии с ошибками (неизвестный predicate,
неверная дата), прогон валидаторов, regression fixture для разработчиков.
Учебная проверка извлечения, не юридическая консультация.

> Журналы в `ip-law-course/` — **тестовые фикстуры валидатора**, а не одобренная
> калибровочная сессия: `expert_approved=true` в них нужен только для того,
> чтобы последовательность была валидной. Явное одобрение в `/legal-calibration`
> эксперт делает сам.

## Источник корректного кейса

Федеральный закон от 02.07.2013 № 187-ФЗ «О внесении изменений в отдельные
законодательные акты Российской Федерации по вопросам защиты интеллектуальных
прав в информационно-телекоммуникационных сетях».

- Координата: **ст. 4 п. 2** — «статью 1302 дополнить пунктом 3 следующего
  содержания» (ст. 4 вносит изменения в часть четвёртую ГК РФ).
- Вступление в силу: ст. 5 — «вступает в силу с 1 августа 2013 года».
- Сверено по двум публикациям: [WIPO Lex](https://www.wipo.int/wipolex/ru/text/442456)
  и [«Российская газета»](https://rg.ru/2013/07/10/pravo-internet-dok.html)
  (официальная публикация от 09.07.2013). Тексты совпадают.
- predicate `дополнена`: существующая статья 1302 дополнена новым пунктом.
  `effective_to = null`: акт не устанавливает срок окончания действия изменения.

## Варианты

| Файл | Отличие от корректного кейса |
|---|---|
| `valid.jsonl` | — |
| `invalid_predicate.jsonl` | `expected.predicate = "заменена"` (вне канонического списка) |
| `invalid_date.jsonl` | `expected.effective_from = "2013-02-30"` (формат верный, даты нет) |
| `ip-law-course/*.session.jsonl` | те же три варианта как журналы сессии курса; поле изменено в событии `final`, `event_id` пересчитан |

## Результат

| Вариант | harness `validate-cases` | harness JSON Schema | курс `validate` | Ожидается |
|---|---|---|---|---|
| valid | принят | принят | принят | принят ✅ |
| invalid_predicate | отклонён | отклонён | отклонён | отклонён ✅ |
| invalid_date | **принят** | **принят** | **принят** | отклонён ❌ |

Курс проверен на `visualcomments/ip-law-course@2d136c1`.

## Ошибки для разработчиков

### 1. Календарно невозможная дата проходит проверку (оба репозитория)

Проверяется только формат `ГГГГ[-ММ[-ДД]]` регулярным выражением:
- harness: `src/legal_agent_harness/core.py:70` (`_DATE_RE`), `:103`
  (`_is_optional_date`), `schemas/calibration_case.schema.json` (`pattern`);
- курс: `legal-agent-calibration/session.py:70` (`DATE_RE`), `:140`
  (`_is_date_or_null`); CLI `append --effective-from 2013-02-30` тоже принимает.

**Ожидаемое поведение:** во всех полях дат (`source.effective_date`,
`expected/payload.effective_from`, `effective_to`) `ГГГГ-ММ-ДД` должна быть
существующей датой (`datetime.date.fromisoformat`), у `ГГГГ-ММ` месяц
01–12. Ошибка валидации называет поле, например
`expected.effective_from имеет неверный формат`. В JSON Schema месяц и день
можно сузить через `pattern`; полную календарную проверку оставить коду.

Regression: `invalid_date.jsonl`, `ip-law-course/invalid_date.session.jsonl`,
тест `tests/test_p25_schema_validator.py::test_impossible_date_rejected`
(помечен `xfail(strict=True)`).

### 2. harness: противоречивая хронология кейса проходит `validate-cases`

README обещает, что дата результата не может быть раньше даты источника, а
`effective_to` — раньше `effective_from`. Но `validate_temporal_consistency`
вызывается только для ответа агента в `run_case`. Кейс с противоречивым
эталоном проходит `validate-cases`, хотя совпавший с ним ответ никогда не будет
`approved`.

**Ожидаемое поведение:** `validate_case` отклоняет кейс, если
`expected.effective_from < source.effective_date` или
`expected.effective_to < expected.effective_from`. Даты разной точности стоит
сравнивать по общему префиксу: сейчас `"2013" < "2013-08-01"` при сравнении
строк даёт ложную ошибку.

Regression: `test_inconsistent_chronology_rejected` (две мутации
`valid.jsonl`, `xfail(strict=True)`).

## Попутные расхождения «схема ↔ код» в harness (без фикстур)

- `отсылает_к`, `предоставляет_охрану` и т. п. для `amending_act`: код
  отклоняет, схема принимает, потому что `enum` в схеме не зависит от `doc_type`.
- predicate для остальных `doc_type`: код принимает любую строку, схема — только
  `enum`. Поэтому `examples/cases.jsonl` case-002 (`требует`) и case-003
  (`запрещает`) не проходят собственную схему репозитория.
- У `doc_type` нет списка допустимых значений (в курсе есть `DOC_TYPES`):
  опечатка `amending-act` отключает проверку канонических predicate.

## Воспроизведение

```bash
# harness
python -m pytest -q tests/test_p25_schema_validator.py    # 2 passed, 3 xfailed
python scripts/run_harness.py validate-cases tests/fixtures/p25/invalid_date.jsonl  # OK — баг

# курс (из корня ip-law-course)
python3 legal-agent-calibration/scripts/legal_calibration_session.py validate \
  --journal <legal-agent-harness>/tests/fixtures/p25/ip-law-course/invalid_date.session.jsonl
```

Когда ошибку 1 или 2 исправят, соответствующий тест станет XPASS и упадёт:
снимите маркер `xfail`.
