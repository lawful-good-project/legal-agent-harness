"""P25: позитивный и негативные кейсы для валидатора. Сеть не используется.

Фикстуры и описание ожидаемого поведения: tests/fixtures/p25/README.md.
Тесты с xfail(strict=True) фиксируют найденные баги: когда валидатор
исправят, они начнут проходить и упадут как XPASS — тогда снимите маркер.
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


@pytest.mark.xfail(
    strict=True,
    reason="P25: дата 2013-02-30 проходит, проверяется только формат ГГГГ-ММ-ДД",
)
def test_impossible_date_rejected():
    with pytest.raises(CaseValidationError, match="effective_from"):
        load_cases(_fixture("invalid_date"))


@pytest.mark.xfail(
    strict=True,
    reason="P25: хронология кейса не проверяется в validate_case, "
    "такой кейс никогда не будет approved в run_case",
)
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
    with pytest.raises(CaseValidationError):
        validate_case(case)
