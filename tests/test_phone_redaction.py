"""Номера телефонов не попадают в публикуемый отчёт (P41). Сеть не используется.

Regression: раньше `Redactor` вырезал email, но оставлял номер телефона:
«пишите expert@example.com или звоните +7 (900) 000-00-00» превращалось в
«пишите [REDACTED] или звоните +7 (900) 000-00-00», и номер уходил в `--out`.
Теперь номера любых стран заменяются на ``[REDACTED]``, а даты, реквизиты
актов, номера норм, ИНН/ОГРН и суммы остаются без изменений.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from legal_agent_harness import load_cases, redact, run_case  # noqa: E402
from legal_agent_harness.adapters import ReplayAdapter  # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples")

# Вымышленные номера из зарезервированных диапазонов (900 000…, 555-01xx).
PHONES = [
    "+7 (900) 000-00-00",
    "+79000000000",
    "8 (900) 000-00-00",
    "8 900 000 00 00",
    "8-900-000-00-00",
    "+1 202 555 0123",
    "+1-202-555-0123",
    "(202) 555-0123",
    "202-555-0123",
    "+44 20 7946 0000",
    "020 7946 0000",
    "+49 30 0000000",
    "+33 1 00 00 00 00",
    "+380 44 000 0000",
    "+86 10 0000 0000",
]

LEGAL_VALUES = [
    "ФЗ от 01.07.2017 № 147-ФЗ",
    "от 01.07.2017 147-ФЗ",
    "2017-10-01",
    "2017-10-01 2018-01-01",
    "ст. 1252, 1253, 1254",
    "статьи 125 126 1270",
    "статьи 8 9 10 11 12 13",
    "ст. 8 12 125 1270",
    "п. 3 ч. 2 ст. 1252",
    "гк-рф/ст.1252",
    "Регламент (ЕС) 2016/679",
    "А40-123456/2023",
    "ИНН 7707083893",
    "ОГРН 1027700132195",
    "штраф 1 000 000 000 руб.",
    "35 U.S.C. § 102",
    "ISBN 978-5-04-089866-2",
    "ROSPATENT-100-3.2",
    "amend-147fz-2017-001",
    "(a)(1)",
]


def _amendment_case():
    return load_cases(os.path.join(EXAMPLES, "amendments.jsonl"))[0]


def _leaky_response(case):
    return {
        "expected": dict(case["expected"]),
        "api_key": "test-secret-value",
        "note": "пишите expert@example.com или звоните +7 (900) 000-00-00",
    }


def test_regression_phone_not_in_result():
    case = _amendment_case()
    adapter = ReplayAdapter({case["case_id"]: {"response": _leaky_response(case)}})
    result = run_case(case, adapter)
    blob = json.dumps(result.to_dict(), ensure_ascii=False)
    assert "000-00-00" not in blob
    assert result.output["note"] == "пишите [REDACTED] или звоните [REDACTED]"


@pytest.mark.parametrize("phone", PHONES)
def test_redact_phone_numbers(phone):
    assert redact(f"тел. {phone}, звонить днём") == "тел. [REDACTED], звонить днём"


@pytest.mark.parametrize("value", LEGAL_VALUES)
def test_redact_keeps_legal_numbers(value):
    assert redact(value) == value


@pytest.mark.parametrize("name", ["cases.jsonl", "amendments.jsonl"])
def test_redact_keeps_example_cases(name):
    for case in load_cases(os.path.join(EXAMPLES, name)):
        assert redact(case) == case


def test_cli_out_has_no_secrets_and_keeps_metadata(tmp_path):
    case = _amendment_case()
    cases = tmp_path / "cases.jsonl"
    cases.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    responses = tmp_path / "responses.jsonl"
    responses.write_text(
        json.dumps(
            {"case_id": case["case_id"], "response": _leaky_response(case)},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "run.json"
    proc = subprocess.run(
        [
            sys.executable,
            os.path.join(ROOT, "scripts", "run_harness.py"),
            "replay",
            "--cases",
            str(cases),
            "--responses",
            str(responses),
            "--out",
            str(out),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    report = out.read_text(encoding="utf-8")
    for text in (proc.stdout, report):
        assert "test-secret-value" not in text
        assert "expert@example.com" not in text
        assert "000-00-00" not in text
    # Реквизиты поправки публикуются целиком: норма, тип изменения, даты.
    (result,) = json.loads(report)
    task = next(e for e in result["events"] if e["event"] == "task")
    assert task["payload"]["source"] == case["source"]
    assert result["output"]["expected"] == case["expected"]
    assert result["approved"] is True
    assert result["needs_expert_approval"] is True
