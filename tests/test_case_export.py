"""Экспорт всех записей графа в калибровочные кейсы. Сеть не используется."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from legal_agent_harness import (  # noqa: E402
    CaseValidationError,
    build_cases,
    load_cases,
    validate_case,
)
from legal_agent_harness.validation_app import build_case, extract_graph  # noqa: E402


@pytest.fixture
def graph():
    """Три учебные поправки, включая две к одной статье."""
    text = (
        "Статью 1259 дополнить пунктом 1.\n"
        "Статью 1260 изложить в новой редакции.\n"
        "Статью 1259 дополнить пунктом 2.\n"
    )
    result = extract_graph(
        "учебный-акт.txt", text.encode("utf-8"), "Учебный орган", "RU"
    )
    # Даты задаются отдельно: экспорт сохраняет уже извлечённые значения.
    result["document"]["effective_date"] = "2027-01-01"
    for index, record in enumerate(result["records"]):
        record["effective_from"] = f"2027-0{index + 1}-01"
        record["effective_to"] = "2027-12-31" if index == 0 else None
        record["confidence"] = 0.7 + index / 10
    return result


def _run_cli(args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_harness.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _write_graph(tmp_path, graph):
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
    return path


def test_build_cases_preserves_every_record_and_its_source(graph):
    """Каждая запись получает собственные реквизиты, координату и даты."""
    cases = build_cases(graph)

    assert len(cases) == len(graph["records"]) == 3
    for case, record in zip(cases, graph["records"]):
        assert validate_case(case) is case
        assert case["source"] == {
            "authority": graph["document"]["authority"],
            "jurisdiction": "RU",
            "doc_type": "amending_act",
            "identifier": "учебный-акт.txt",
            "coordinate": record["coordinate"],
            "effective_date": "2027-01-01",
        }
        assert case["expected"] == {
            key: value for key, value in record.items() if key != "coordinate"
        }


def test_case_ids_are_stable_and_distinguish_changes_to_same_norm(graph):
    """Повтор одной нормы не теряет поправку и не создаёт одинаковые case_id."""
    original = copy.deepcopy(graph)
    cases = build_cases(graph)

    assert build_cases(graph) == cases
    assert len({case["case_id"] for case in cases}) == 3
    assert cases[0]["expected"]["norm_id"] == cases[2]["expected"]["norm_id"]
    other_document = copy.deepcopy(graph)
    digest = graph["document"]["sha256"]
    other_document["document"]["sha256"] = digest[:-1] + (
        "0" if digest[-1] != "0" else "1"
    )
    assert {case["case_id"] for case in cases}.isdisjoint(
        case["case_id"] for case in build_cases(other_document)
    )
    cases[0]["source"]["coordinate"] = "изменено в копии"
    cases[0]["expected"]["subject"] = "изменено в копии"
    assert graph == original


def test_build_cases_accepts_empty_records(graph):
    """Пустой граф не превращается в вымышленный кейс."""
    graph["records"] = []
    assert build_cases(graph) == []


def test_single_case_api_keeps_first_record_behavior(graph):
    """Существующий build_case продолжает возвращать первую поправку."""
    single = build_case(graph)
    first = build_cases(graph)[0]
    assert single["case_id"].startswith("upload-")
    assert single["source"] == first["source"]
    assert single["expected"] == first["expected"]


@pytest.mark.parametrize(
    "field", ["effective_from", "effective_to", "change_description"]
)
def test_build_cases_keeps_optional_fields_optional(graph, field):
    """Экспорт не делает необязательные поля обязательными."""
    del graph["records"][1][field]
    cases = build_cases(graph)
    assert field not in cases[1]["expected"]
    assert validate_case(cases[1]) is cases[1]


@pytest.mark.parametrize(
    "value, message",
    [
        ([], "граф"),
        ({}, "document"),
        ({"document": {}, "records": {}}, "records"),
        ({"document": {}, "records": []}, "sha256"),
    ],
)
def test_build_cases_reports_invalid_graph(value, message):
    """Неверная структура вызывает понятную ошибку валидации."""
    with pytest.raises(CaseValidationError, match=message):
        build_cases(value)


@pytest.mark.parametrize(
    "field, value", [("predicate", "упомянута"), ("confidence", None)]
)
def test_build_cases_validates_later_records(graph, field, value):
    """Ошибки после первой записи не пропускаются молча."""
    graph["records"][2][field] = value
    with pytest.raises(CaseValidationError, match=field):
        build_cases(graph)


@pytest.mark.parametrize("save_file", [False, True])
def test_cli_exports_loadable_jsonl_and_keeps_expert_gate(graph, tmp_path, save_file):
    """JSONL читается harness; совпадение всех кейсов по-прежнему требует эксперта."""
    path = _write_graph(tmp_path, graph)
    output = tmp_path / "cases.jsonl"
    args = ["export-cases", str(path)]
    if save_file:
        args += ["--out", str(output)]
    proc = _run_cli(args)

    assert proc.returncode == 0, proc.stderr
    assert "3" in proc.stderr and "эксперт" in proc.stderr
    if save_file:
        assert output.read_text(encoding="utf-8") == proc.stdout
    else:
        output.write_text(proc.stdout, encoding="utf-8")
    cases = load_cases(str(output))
    assert cases == build_cases(graph)
    responses = tmp_path / "responses.jsonl"
    responses.write_text(
        "".join(
            json.dumps(
                {"case_id": c["case_id"], "response": {"expected": c["expected"]}}
            )
            + "\n"
            for c in cases
        ),
        encoding="utf-8",
    )
    replay = _run_cli(["replay", "--cases", str(output), "--responses", str(responses)])
    assert replay.returncode == 0, replay.stderr
    results = json.loads(replay.stdout)
    assert len(results) == 3
    assert all(r["approved"] and r["needs_expert_approval"] for r in results)
    assert all(r["events"][-1]["payload"]["needs_expert_approval"] for r in results)


def test_cli_does_not_truncate_output_when_later_record_is_invalid(graph, tmp_path):
    """Весь граф проверяется до перезаписи файла назначения."""
    graph["records"][2]["coordinate"] = ""
    path = _write_graph(tmp_path, graph)
    output = tmp_path / "cases.jsonl"
    output.write_text("сохранённый результат\n", encoding="utf-8")
    proc = _run_cli(["export-cases", str(path), "--out", str(output)])
    assert proc.returncode == 1
    assert "coordinate" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert proc.stdout == ""
    assert output.read_text(encoding="utf-8") == "сохранённый результат\n"


def test_cli_preserves_output_when_graph_contains_invalid_unicode(graph, tmp_path):
    """Некорректный Unicode отклоняется до открытия выходного файла."""
    graph["records"][1]["change_description"] = "\ud800"
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    output = tmp_path / "cases.jsonl"
    output.write_text("сохранённый результат\n", encoding="utf-8")

    proc = _run_cli(["export-cases", str(path), "--out", str(output)])

    assert proc.returncode == 1
    assert "ОШИБКА экспорта" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert proc.stdout == ""
    assert output.read_text(encoding="utf-8") == "сохранённый результат\n"


@pytest.mark.parametrize("text", ['{"document":{},"document":{}}', '{"document":'])
def test_cli_rejects_ambiguous_or_broken_json(tmp_path, text):
    """Повторяющиеся ключи и синтаксические ошибки не дают частичный экспорт."""
    path = tmp_path / "graph.json"
    path.write_text(text, encoding="utf-8")
    proc = _run_cli(["export-cases", str(path)])
    assert proc.returncode == 1
    assert "ОШИБКА" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert proc.stdout == ""


def test_cli_redacts_secrets_and_preserves_legal_metadata(graph, tmp_path):
    """В stdout и файле секреты вырезаны, а реквизиты поправок сохранены."""
    token = "hf_" + "A1b2" * 8
    graph["records"][1]["change_description"] = f"Проверить: expert@example.com {token}"
    path = _write_graph(tmp_path, graph)
    output = tmp_path / "cases.jsonl"
    proc = _run_cli(["export-cases", str(path), "--out", str(output)])
    assert proc.returncode == 0, proc.stderr
    for text in (proc.stdout, output.read_text(encoding="utf-8")):
        assert token not in text
        assert "expert@example.com" not in text
        assert "[REDACTED]" in text
    cases = load_cases(str(output))
    for case, original in zip(cases, build_cases(graph)):
        assert case["case_id"] == original["case_id"]
        assert case["source"] == original["source"]
        for field in ("norm_id", "predicate", "effective_from", "effective_to"):
            assert case["expected"][field] == original["expected"][field]
