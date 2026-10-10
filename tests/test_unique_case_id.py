"""Уникальность case_id в файлах кейсов и ответов. Сеть не используется."""

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

from legal_agent_harness import (  # noqa: E402
    CaseValidationError,
    ResponseValidationError,
    load_cases,
    load_responses,
)


def _case(case_id: str) -> dict:
    return {
        "case_id": case_id,
        "source": {
            "authority": "Федеральное Собрание РФ",
            "jurisdiction": "RU",
            "doc_type": "amending_act",
            "identifier": "ФЗ № 35-ФЗ",
            "coordinate": "ст. 1 п. 1",
            "effective_date": "2014-10-01",
        },
        "expected": {
            "norm_id": "GK-RF-1335.1",
            "predicate": "введена",
            "subject": "ГК РФ ст. 1335.1",
            "object": "право изготовителя базы данных",
            "confidence": 0.9,
        },
    }


def _response(case_id: str) -> dict:
    return {"case_id": case_id, "response": {"status": "ok", "expected": _case(case_id)["expected"]}}


def _write_jsonl(path, objects) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        for obj in objects:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    return str(path)


def test_load_cases_accepts_unique_ids(tmp_path):
    path = _write_jsonl(tmp_path / "cases.jsonl", [_case("a-1"), _case("a-2")])
    assert [c["case_id"] for c in load_cases(path)] == ["a-1", "a-2"]


def test_load_cases_rejects_duplicate_case_id(tmp_path):
    path = _write_jsonl(
        tmp_path / "cases.jsonl", [_case("a-1"), _case("a-2"), _case("a-1")]
    )
    with pytest.raises(CaseValidationError) as excinfo:
        load_cases(path)
    message = str(excinfo.value)
    assert "дублирующийся case_id 'a-1'" in message
    assert ":3:" in message  # где найден дубликат
    assert "записи 1" in message  # где встретился впервые


def test_load_responses_rejects_duplicate_case_id(tmp_path):
    path = _write_jsonl(
        tmp_path / "responses.jsonl", [_response("a-1"), _response("a-1")]
    )
    with pytest.raises(ResponseValidationError) as excinfo:
        load_responses(path)
    assert "дублирующийся case_id 'a-1'" in str(excinfo.value)


def test_load_responses_accepts_unique_ids(tmp_path):
    path = _write_jsonl(
        tmp_path / "responses.jsonl", [_response("a-1"), _response("a-2")]
    )
    assert set(load_responses(path)) == {"a-1", "a-2"}


def test_cli_validate_cases_reports_duplicate(tmp_path):
    path = _write_jsonl(tmp_path / "cases.jsonl", [_case("a-1"), _case("a-1")])
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "run_harness.py"), "validate-cases", path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 1
    assert "дублирующийся case_id" in proc.stderr


def test_cli_replay_rejects_duplicate_response(tmp_path):
    cases = _write_jsonl(tmp_path / "cases.jsonl", [_case("a-1")])
    responses = _write_jsonl(
        tmp_path / "responses.jsonl", [_response("a-1"), _response("a-1")]
    )
    proc = subprocess.run(
        [
            sys.executable,
            os.path.join(ROOT, "scripts", "run_harness.py"),
            "replay",
            "--cases",
            cases,
            "--responses",
            responses,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 1
    assert "дублирующийся case_id" in proc.stderr
