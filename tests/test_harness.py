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
    build_agent_task,
    redact,
    run_case,
    validate_case,
    validate_temporal_consistency,
)
from legal_agent_harness.adapters import (  # noqa: E402
    AdapterError,
    LocalProcessAdapter,
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
        "api_key": "test-secret-value",
        "note": "пишите на expert@example.com",
        "header": "Authorization: test-secret-value",
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


def _leaky_response(case):
    return {
        "expected": dict(case["expected"]),
        "api_key": "test-secret-value",
        "note": "пишите на expert@example.com",
    }


def test_output_has_no_secrets():
    case = _case()
    adapter = ReplayAdapter({"case-001": {"response": _leaky_response(case)}})
    result = run_case(case, adapter)
    assert result.approved is True
    blob = json.dumps(result.to_dict(), ensure_ascii=False)
    assert "test-secret-value" not in blob
    assert "expert@example.com" not in blob
    assert result.output["api_key"] == "[REDACTED]"
    assert result.output["expected"] == case["expected"]


def test_cli_out_and_stdout_have_no_secrets(tmp_path):
    case = _case()
    cases = tmp_path / "cases.jsonl"
    cases.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    responses = tmp_path / "responses.jsonl"
    responses.write_text(
        json.dumps({"case_id": "case-001", "response": _leaky_response(case)}) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "run.json"
    proc = _run_cli(
        ["replay", "--cases", str(cases), "--responses", str(responses), "--out", str(out)]
    )
    assert proc.returncode == 0
    for text in (proc.stdout, out.read_text(encoding="utf-8")):
        assert "test-secret-value" not in text
        assert "expert@example.com" not in text


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


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("confidence", "0.9", "confidence должен быть числом"),
        ("confidence", True, "confidence должен быть числом"),
        ("confidence", float("nan"), r"confidence должен быть в диапазоне"),
        ("norm_id", 1252, "непустыми строками"),
        ("subject", ["ГК РФ ст. 1252"], "непустыми строками"),
        ("object", "   ", "непустыми строками"),
        ("effective_from", "вчера", "effective_from имеет неверный формат"),
        ("effective_from", "01.10.2017", "effective_from имеет неверный формат"),
        ("effective_to", 2018, "effective_to имеет неверный формат"),
        ("change_description", 42, "change_description"),
        ("change_description", "х" * 2001, "change_description"),
    ],
)
def test_validate_case_rejects_wrong_expected_types(field, value, message):
    case = _amendment_case()
    case["expected"][field] = value
    with pytest.raises(CaseValidationError, match=message):
        validate_case(case)


@pytest.mark.parametrize(
    "field, value",
    [
        ("confidence", 1),
        ("confidence", 0.0),
        ("effective_from", "2017"),
        ("effective_from", "2017-10"),
        ("effective_to", None),
        ("change_description", None),
        ("change_description", "х" * 2000),
    ],
)
def test_validate_case_accepts_valid_expected_values(field, value):
    case = _amendment_case()
    case["expected"][field] = value
    assert validate_case(case) == case


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


def _python_agent(code):
    return LocalProcessAdapter([sys.executable, "-c", code], timeout=10)


def test_run_case_does_not_pass_expected_to_adapter():
    case = _amendment_case()
    seen = []

    class _SpyAdapter:
        def respond(self, task):
            seen.append(task)
            return {"expected": case["expected"]}

    run_case(case, _SpyAdapter())
    assert seen == [build_agent_task(case)]
    assert sorted(seen[0]) == ["case_id", "source"]


def test_local_process_echo_agent_is_not_approved():
    case = _amendment_case()
    adapter = _python_agent(
        "import sys\n"
        "sys.stdout.buffer.write(sys.stdin.buffer.read())\n"
    )
    result = run_case(case, adapter, max_rounds=1)
    assert result.approved is False


def test_local_process_agent_answer_is_approved():
    case = _amendment_case()
    answer = json.dumps({"expected": case["expected"]}, ensure_ascii=True)
    adapter = _python_agent(
        "import json, sys\n"
        "task = json.loads(sys.stdin.buffer.read().decode('utf-8'))\n"
        "assert task['source']['identifier'] == 'ФЗ от 01.07.2017 № 147-ФЗ'\n"
        f"print({answer!r})\n"
    )
    result = run_case(case, adapter)
    assert result.approved is True
    assert result.rounds == 1


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


@pytest.fixture
def report_input(tmp_path):
    results = [
        {
            "case_id": "matched",
            "status": "approved",
            "rounds": 1,
            "approved": True,
            "needs_expert_approval": True,
            "events": [],
            "output": {},
        },
        {
            "case_id": "unmatched",
            "status": "needs_expert_approval",
            "rounds": 3,
            "approved": False,
            "needs_expert_approval": True,
            "events": [],
            "output": {},
        },
    ]
    path = tmp_path / "report.json"
    path.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    return path


def test_cli_report_values_and_format(report_input):
    proc = _run_cli(["report", str(report_input)])
    assert proc.returncode == 0
    assert proc.stderr == ""
    assert proc.stdout == (
        "Отчёт о калибровке\n"
        "  кейсов:              2\n"
        "  совпало:             1\n"
        "  ждут эксперта:       2\n"
        "  среднее число раундов: 2.00\n"
        "  OK matched status=approved rounds=1\n"
        "  !! unmatched status=needs_expert_approval rounds=3\n"
    )


def test_cli_no_command_returns_error():
    proc = _run_cli([])
    assert proc.returncode == 1


def test_amendment_nan_confidence_prevents_approval():
    fixture = os.path.join(ROOT, "tests", "fixtures", "llm_nan_response.json")
    with open(fixture, encoding="utf-8") as fh:
        recorded_confidence = json.load(fh)["expected"]["confidence"]
    case = _amendment_case()
    for confidence in (recorded_confidence, "NaN"):
        response = {"expected": dict(case["expected"], confidence=confidence)}
        adapter = ReplayAdapter({case["case_id"]: {"response": response}})
        result = run_case(case, adapter)
        assert result.approved is False, confidence


def test_confidence_comparison_preserves_tolerance():
    case = _amendment_case()
    for confidence, approved in [(0.9 + 5e-10, True), (0.9 + 2e-9, False)]:
        response = {"expected": dict(case["expected"], confidence=confidence)}
        adapter = ReplayAdapter({case["case_id"]: {"response": response}})
        result = run_case(case, adapter)
        assert result.approved is approved, confidence
