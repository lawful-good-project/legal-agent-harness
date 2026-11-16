"""Контракт ответа агента: примеры из examples/ и их описание в docs/.

Принятый пример должен проходить валидацию и replay, пример без coordinate —
отклоняться до запуска агента, а документ контракта — показывать те же данные,
что лежат в examples/. Сеть не используется.
"""

from __future__ import annotations

import copy
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
    build_agent_task,
    load_cases,
    load_responses,
    redact,
    run_case,
    validate_case,
    validate_response,
)
from legal_agent_harness.adapters import ReplayAdapter  # noqa: E402
from legal_agent_harness.core import (  # noqa: E402
    AMENDMENT_PREDICATES,
    EXPECTED_ALLOWED_FIELDS,
)

CASES = os.path.join(ROOT, "examples", "agent_contract.jsonl")
RESPONSES = os.path.join(ROOT, "examples", "agent_contract_responses.jsonl")
REJECTED = os.path.join(ROOT, "examples", "agent_contract_rejected.jsonl")
CONTRACT_DOC = os.path.join(ROOT, "docs", "AGENT_RESPONSE_CONTRACT.md")
PROFILE_DOC = os.path.join(ROOT, "docs", "AMENDMENT-EXTRACTION.md")

# «Одно-два предложения своими словами», а не текст нормы.
MAX_DESCRIPTION_LENGTH = 300


class _CountingReplay(ReplayAdapter):
    """Replay-адаптер, который считает обращения к агенту."""

    calls = 0

    def respond(self, case):
        self.calls += 1
        return super().respond(case)


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _accepted():
    (case,) = load_cases(CASES)
    return case, load_responses(RESPONSES)[case["case_id"]]["response"]


def _rejected():
    # Читаем напрямую: load_cases такой кейс справедливо не пропускает.
    (line,) = [ln for ln in _read(REJECTED).splitlines() if ln.strip()]
    return json.loads(line)


def test_accepted_example_follows_contract():
    case, response = _accepted()
    source, answer = case["source"], response["expected"]

    assert source["doc_type"] == "amending_act"
    assert source["coordinate"].strip()
    assert set(response) == {"status", "expected"}
    assert set(answer) == EXPECTED_ALLOWED_FIELDS
    assert answer["predicate"] in AMENDMENT_PREDICATES
    # Изменяющий акт в ответе — тот же, что указан в источнике.
    assert answer["object"] == source["identifier"]
    assert answer["effective_from"] >= source["effective_date"]

    validate_response(response)
    # Поля ответа выдерживают ту же строгую проверку, что и эталон кейса.
    validate_case({**case, "expected": answer})


def test_accepted_example_replays_but_still_needs_expert():
    case, _ = _accepted()
    result = run_case(case, ReplayAdapter(load_responses(RESPONSES)))

    assert result.approved is True
    assert result.rounds == 1
    assert result.needs_expert_approval is True
    assert result.events[-1]["event"] == "final"
    assert result.events[-1]["payload"]["needs_expert_approval"] is True


def test_value_without_coordinate_is_rejected_before_agent():
    case, response = _accepted()
    rejected = _rejected()

    # То же значение и тот же источник; не хватает только coordinate.
    assert "coordinate" not in rejected["source"]
    assert rejected["expected"] == case["expected"]
    restored = dict(rejected["source"], coordinate=case["source"]["coordinate"])
    assert restored == case["source"]

    adapter = _CountingReplay({rejected["case_id"]: {"response": response}})
    with pytest.raises(CaseValidationError, match="coordinate"):
        run_case(rejected, adapter)
    assert adapter.calls == 0

    with pytest.raises(CaseValidationError, match="coordinate"):
        load_cases(REJECTED)


@pytest.mark.parametrize("coordinate", ["", "   ", None])
def test_blank_coordinate_is_rejected(coordinate):
    case, _ = _accepted()
    broken = copy.deepcopy(case)
    broken["source"]["coordinate"] = coordinate

    with pytest.raises(CaseValidationError, match="coordinate"):
        validate_case(broken)


def test_examples_have_no_full_text_or_private_data():
    case, response = _accepted()
    for example in (case, response, _rejected()):
        # Redactor не находит токенов, e-mail и секретных ключей.
        assert redact(example) == example
        description = example["expected"]["change_description"]
        assert len(description) <= MAX_DESCRIPTION_LENGTH


def test_contract_doc_shows_the_same_examples():
    case, response = _accepted()
    doc = _read(CONTRACT_DOC)
    blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", doc, re.S)]

    assert build_agent_task(case) in blocks
    assert response in blocks
    assert build_agent_task(_rejected()) in blocks


def test_profile_doc_links_contract_and_examples():
    profile, contract = _read(PROFILE_DOC), _read(CONTRACT_DOC)

    assert os.path.basename(CONTRACT_DOC) in profile
    assert os.path.basename(PROFILE_DOC) in contract
    for path in (CASES, RESPONSES, REJECTED):
        assert os.path.isfile(path)
        name = "examples/" + os.path.basename(path)
        assert name in profile
        assert name in contract
