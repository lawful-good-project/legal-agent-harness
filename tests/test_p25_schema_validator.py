"""P25: позитивный и негативные кейсы для валидатора. Сеть не используется.

Фикстуры и описание ожидаемого поведения: tests/fixtures/p25/README.md.
Обе найденные ошибки (невозможная дата и противоречивая хронология кейса)
исправлены, маркеры xfail сняты.
"""

from __future__ import annotations

import copy
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from legal_agent_harness import CaseValidationError, load_cases, validate_case  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures", "p25")


def _fixture(name: str) -> str:
    return os.path.join(FIXTURES, name + ".jsonl")


def test_valid_case_accepted():
    (case,) = load_cases(_fixture("valid"))
    assert case["expected"]["predicate"] == "дополнена"


def test_unknown_predicate_rejected():
    with pytest.raises(CaseValidationError, match="predicate"):
        load_cases(_fixture("invalid_predicate"))


def test_impossible_date_rejected():
    """Дата 2013-02-30 отвергается (календарная проверка добавлена в P25)."""
    with pytest.raises(CaseValidationError, match="effective_from"):
        load_cases(_fixture("invalid_date"))


@pytest.mark.parametrize(
    "field, value",
    [
        ("effective_from", "2013-07-01"),  # раньше source.effective_date
        ("effective_to", "2013-07-31"),  # раньше effective_from
    ],
)
def test_inconsistent_chronology_rejected(field, value):
    (case,) = load_cases(_fixture("valid"))
    case = copy.deepcopy(case)
    case["expected"][field] = value
    with pytest.raises(CaseValidationError, match=f"хронология: {field}"):
        validate_case(case)


@pytest.mark.parametrize(
    "field, value",
    [
        ("effective_from", "2013"),  # год источника: противоречия нет
        ("effective_from", "2013-08"),  # месяц источника: противоречия нет
        ("effective_to", None),  # отсутствие даты — не ошибка
    ],
)
def test_partial_or_missing_dates_are_not_a_chronology_error(field, value):
    (case,) = load_cases(_fixture("valid"))
    case = copy.deepcopy(case)
    case["expected"][field] = value
    assert validate_case(case) is case
