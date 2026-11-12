"""Даты кейсов: сравнение дат разной точности и календарь. Сеть не используется."""

from __future__ import annotations

import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from legal_agent_harness import (  # noqa: E402
    CaseValidationError,
    ReplayAdapter,
    run_case,
    validate_case,
    validate_temporal_consistency,
)

SCHEMA = os.path.join(ROOT, "schemas", "calibration_case.schema.json")


def _case(source_date="2017-10-01", effective_from="2017-10-01", effective_to=None):
    return {
        "case_id": "amend-147fz-2017-001",
        "source": {
            "authority": "Государственная Дума ФС РФ",
            "jurisdiction": "RU",
            "doc_type": "amending_act",
            "identifier": "ФЗ от 01.07.2017 № 147-ФЗ",
            "coordinate": "ст. 1",
            "effective_date": source_date,
        },
        "expected": {
            "norm_id": "гк-рф/ст.1252",
            "predicate": "изменена",
            "subject": "ГК РФ ст. 1252",
            "object": "ФЗ-147 от 01.07.2017",
            "effective_from": effective_from,
            "effective_to": effective_to,
            "change_description": "Статья изменена федеральным законом.",
            "confidence": 0.9,
        },
    }


def _chronology_errors(case):
    return validate_temporal_consistency(case, {"expected": case["expected"]})


def test_answer_equal_to_month_precision_expected_is_approved():
    # Эталон известен с точностью до месяца, дата источника — до дня. Раньше
    # ответ, совпадающий с эталоном, отклонялся во всех раундах:
    # «effective_from 2017-10 раньше даты источника 2017-10-01».
    case = _case(source_date="2017-10-01", effective_from="2017-10")
    answer = {"status": "ok", "expected": dict(case["expected"])}
    result = run_case(case, ReplayAdapter({case["case_id"]: {"response": answer}}))
    assert result.approved is True
    assert result.rounds == 1


@pytest.mark.parametrize(
    "effective_from, source_date",
    [
        ("2017-10", "2017-10-01"),  # 1 октября входит в октябрь
        ("2017-10", "2017-10-15"),  # день внутри октября неизвестен
        ("2017", "2017-10-15"),
        ("2017-10-01", "2017-10"),
        ("2018", "2017-10-01"),
    ],
)
def test_dates_of_different_precision_without_contradiction(
    effective_from, source_date
):
    case = _case(source_date=source_date, effective_from=effective_from)
    assert _chronology_errors(case) == []


@pytest.mark.parametrize(
    "effective_from, source_date",
    [
        ("2017-09", "2017-10-01"),  # весь сентябрь раньше 1 октября
        ("2017-09-30", "2017-10"),
        ("2016", "2017-03"),
        ("2017", "2018-01-01"),
    ],
)
def test_dates_of_different_precision_with_contradiction(effective_from, source_date):
    case = _case(source_date=source_date, effective_from=effective_from)
    errors = _chronology_errors(case)
    assert len(errors) == 1
    assert "раньше даты источника" in errors[0]


@pytest.mark.parametrize(
    "effective_from, effective_to, contradiction",
    [
        ("2017-10-15", "2017-10", False),
        ("2017-10", "2017-10-31", False),
        ("2017-10", "2017", False),
        ("2017-10-15", "2017-09", True),
        ("2018-01", "2017", True),
    ],
)
def test_effective_to_of_different_precision(
    effective_from, effective_to, contradiction
):
    case = _case(
        source_date="2017", effective_from=effective_from, effective_to=effective_to
    )
    errors = _chronology_errors(case)
    assert bool(errors) is contradiction
    if contradiction:
        assert "effective_to" in errors[0]


@pytest.mark.parametrize(
    "value",
    [
        "2017-13",
        "2017-00",
        "2017-13-45",
        "2017-02-30",
        "2019-02-29",
        "2017-04-31",
        "0000",
    ],
)
@pytest.mark.parametrize("field", ["effective_from", "effective_to"])
def test_validate_case_rejects_impossible_expected_dates(field, value):
    case = _case()
    case["expected"][field] = value
    with pytest.raises(
        CaseValidationError, match=f"{field} .*такой даты не существует"
    ):
        validate_case(case)


@pytest.mark.parametrize("value", ["2017-02-30", "2017-13"])
def test_validate_case_rejects_impossible_source_date(value):
    case = _case(source_date=value)
    with pytest.raises(
        CaseValidationError, match="effective_date .*такой даты не существует"
    ):
        validate_case(case)


@pytest.mark.parametrize("value", ["2020-02-29", "2017-12-31", "2017-12", "2017", None])
def test_validate_case_accepts_real_dates(value):
    case = _case(source_date=None, effective_from=value)
    assert validate_case(case) == case


def test_schema_date_pattern_rejects_impossible_months_and_days():
    with open(SCHEMA, encoding="utf-8") as fh:
        schema = json.load(fh)
    patterns = {
        schema["properties"]["source"]["properties"]["effective_date"]["pattern"],
        schema["properties"]["expected"]["properties"]["effective_from"]["pattern"],
        schema["properties"]["expected"]["properties"]["effective_to"]["pattern"],
    }
    assert len(patterns) == 1
    pattern = re.compile(patterns.pop())
    for value in ("2017", "2017-12", "2017-12-31", "2020-02-29"):
        assert pattern.search(value), value
    # 30 февраля шаблоном не выразить: такую дату отсеивает validate_case.
    for value in ("2017-13", "2017-00", "2017-13-45", "2017-10-32", "2017-10-00"):
        assert not pattern.search(value), value
