"""Модульные тесты harness. Сеть не используется."""

from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from legal_agent_harness import (  # noqa: E402
    CaseValidationError,
    Redactor,
    redact,
    run_case,
    validate_case,
    validate_temporal_consistency,
)
from legal_agent_harness.adapters import (  # noqa: E402
    AdapterError,
    ReplayAdapter,
    get_adapter,
)

EXAMPLES = os.path.join(ROOT, "examples")


def _case():
    return {
        "case_id": "case-001",
        "source": {
            "authority": "ВОИС",
            "jurisdiction": "RU",
            "doc_type": "statute",
            "identifier": "ГК РФ ст. 1259",
            "coordinate": "п. 1",
        },
        "expected": {
            "norm_id": "GK-RF-1259",
            "predicate": "предоставляет_охрану",
            "subject": "произведения науки",
            "object": "авторское право",
            "effective_from": "2008-01-01",
            "effective_to": None,
            "confidence": 0.9,
        },
    }


def _amendment_case():
    case = _case()
    case["case_id"] = "amendment-001"
    case["source"].update(
        {
            "doc_type": "amending_act",
            "identifier": "ФЗ от 01.07.2017 № 147-ФЗ",
            "effective_date": "2017-10-01",
        }
    )
    case["expected"].update(
        {
            "norm_id": "гк-рф/ст.1252",
            "predicate": "изменена",
            "subject": "ГК РФ ст. 1252",
            "object": "ФЗ-147 от 01.07.2017",
            "effective_from": "2017-10-01",
            "change_description": "Статья изменена федеральным законом.",
        }
    )
    return case


class _CountingAdapter:
    """Адаптер, который отдаёт ответы по очереди и считает вызовы."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def respond(self, case):
        self.calls += 1
        idx = min(self.calls - 1, len(self._responses) - 1)
        return self._responses[idx]


def test_success_first_round():
    case = _case()
    adapter = ReplayAdapter({"case-001": {"response": {"expected": case["expected"]}}})
    result = run_case(case, adapter)
    assert result.approved is True
    assert result.status == "approved"
    assert result.rounds == 1
    assert [e["event"] for e in result.events] == [
        "task",
        "agent_answer",
        "validator_feedback",
        "expert_review",
        "final",
    ]


def test_failure_after_max_rounds():
    case = _case()
    wrong = dict(case["expected"])
    wrong["norm_id"] = "WRONG"
    adapter = ReplayAdapter({"case-001": {"response": {"expected": wrong}}})
    result = run_case(case, adapter, max_rounds=3)
    assert result.approved is False
    assert result.status == "needs_expert_approval"
    assert result.rounds == 3
    assert sum(1 for e in result.events if e["event"] == "agent_answer") == 3
    assert result.needs_expert_approval is True


def test_final_always_needs_expert_approval():
    case = _case()
    adapter = ReplayAdapter({"case-001": {"response": {"expected": case["expected"]}}})
    result = run_case(case, adapter)
    final = [e for e in result.events if e["event"] == "final"][0]
    assert final["payload"]["needs_expert_approval"] is True
    assert result.needs_expert_approval is True


def test_redaction_of_tokens_and_emails():
    payload = {
        "api_key": "sk-abcdef0123456789abcdef0123456789",
        "note": "пишите на expert@example.com",
        "header": "Authorization: Bearer abcdef0123456789abcdef0123456789",
    }
    cleaned = redact(payload)
    assert cleaned["api_key"] == "[REDACTED]"
    assert "expert@example.com" not in json.dumps(cleaned, ensure_ascii=False)
    assert "[REDACTED]" in cleaned["note"]


def test_events_have_no_secrets():
    case = _case()
    case["source"]["identifier"] = (
        "secret expert@example.com token=abcdef0123456789abcdef0123456789"
    )
    adapter = ReplayAdapter({"case-001": {"response": {"expected": case["expected"]}}})
    result = run_case(case, adapter, redactor=Redactor())
    blob = json.dumps(result.events, ensure_ascii=False)
    assert "expert@example.com" not in blob
    assert "abcdef0123456789abcdef0123456789" not in blob


def test_validate_case_rejects_missing_source_field():
    case = _case()
    del case["source"]["coordinate"]
    with pytest.raises(CaseValidationError):
        validate_case(case)


def test_validate_case_rejects_extra_expected_field():
    case = _case()
    case["expected"]["extra"] = "x"
    with pytest.raises(CaseValidationError):
        validate_case(case)


def test_amendment_case_accepts_effective_date_and_description():
    case = _amendment_case()
    assert validate_case(case) == case


def test_temporal_consistency_rejects_inverted_dates():
    case = _amendment_case()
    response = {"expected": dict(case["expected"], effective_to="2017-01-01")}
    errors = validate_temporal_consistency(case, response)
    assert errors
    assert "effective_to" in errors[0]


def test_temporal_consistency_rejects_date_before_source():
    case = _amendment_case()
    response = {"expected": dict(case["expected"], effective_from="2017-09-30")}
    errors = validate_temporal_consistency(case, response)
    assert errors
    assert "раньше даты источника" in errors[0]


def test_amendment_predicate_must_be_canonical():
    case = _amendment_case()
    case["expected"]["predicate"] = "упомянута"
    with pytest.raises(CaseValidationError, match="predicate"):
        validate_case(case)


def test_effective_date_format_is_checked():
    case = _amendment_case()
    case["source"]["effective_date"] = "не дата"
    with pytest.raises(CaseValidationError, match="effective_date"):
        validate_case(case)


def test_amendment_temporal_error_prevents_approval():
    case = _amendment_case()
    wrong = dict(case["expected"], effective_from="2017-09-30")
    adapter = ReplayAdapter({"amendment-001": {"response": {"expected": wrong}}})
    result = run_case(case, adapter)
    assert result.approved is False
    assert result.status == "needs_expert_approval"


def test_replay_adapter_returns_supplied_json():
    case = _case()
    payload = {"expected": dict(case["expected"])}
    adapter = ReplayAdapter({"case-001": {"response": payload}})
    assert adapter.respond(case) == payload


def test_replay_adapter_missing_case():
    with pytest.raises(AdapterError):
        ReplayAdapter({}).respond(_case())


def test_get_adapter_local_process_requires_command():
    with pytest.raises(AdapterError):
        get_adapter("local-process")


def test_examples_load_and_pass():
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from legal_agent_harness import load_cases, load_responses

    cases = load_cases(os.path.join(EXAMPLES, "cases.jsonl"))
    responses = load_responses(os.path.join(EXAMPLES, "responses.jsonl"))
    assert len(cases) == 3
    adapter = ReplayAdapter(responses)
    for case in cases:
        result = run_case(case, adapter)
        assert result.approved is True
        assert result.needs_expert_approval is True


def _run_cli(args):
    import subprocess

    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "run_harness.py"), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        check=False,
    )


def test_cli_help():
    proc = _run_cli(["--help"])
    assert proc.returncode == 0
    assert "validate-cases" in proc.stdout
    assert "replay" in proc.stdout


def test_cli_validate_cases_exit_status():
    proc = _run_cli(["validate-cases", os.path.join("examples", "cases.jsonl")])
    assert proc.returncode == 0
    assert "3" in proc.stdout


def test_cli_replay_exit_status():
    proc = _run_cli(
        [
            "replay",
            "--cases",
            os.path.join("examples", "cases.jsonl"),
            "--responses",
            os.path.join("examples", "responses.jsonl"),
        ]
    )
    assert proc.returncode == 0
    assert "needs_expert_approval" in proc.stdout


def test_cli_no_command_returns_error():
    proc = _run_cli([])
    assert proc.returncode == 1
