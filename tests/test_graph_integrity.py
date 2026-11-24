"""Уникальность узлов и ссылки графа поправок. Сеть не используется."""

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

from legal_agent_harness import GraphValidationError, validate_graph  # noqa: E402
from legal_agent_harness.validation_app import extract_graph  # noqa: E402


def _graph():
    return {
        "nodes": [{"id": "act:1"}, {"id": "norm:1259"}],
        "edges": [
            {"from": "act:1", "to": "norm:1259"},
            {"from": "act:1", "to": "norm:1259"},
        ],
    }


def _run_cli(args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_harness.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_extraction_keeps_both_changes_and_edges_for_one_norm():
    """Одна норма имеет один узел, а две поправки остаются разными записями."""
    lines = [
        "Статью 1259 дополнить пунктом 1.",
        "Статью 1259 изложить в новой редакции.",
    ]
    graph = extract_graph("demo.txt", "\n".join(lines).encode("utf-8"))

    assert [node["id"] for node in graph["nodes"] if node["type"] == "norm"] == [
        "norm:1259"
    ]
    assert len(graph["records"]) == len(graph["edges"]) == 2
    assert [record["change_description"] for record in graph["records"]] == lines
    assert [record["coordinate"] for record in graph["records"]] == [
        "строка 1",
        "строка 2",
    ]
    assert [edge["relation"] for edge in graph["edges"]] == [
        "дополнена",
        "изложена_в_новой_редакции",
    ]
    assert [edge["evidence"] for edge in graph["edges"]] == lines
    assert all(edge["to"] == "norm:1259" for edge in graph["edges"])
    assert validate_graph(graph) is graph


def test_extraction_preserves_first_occurrence_order_of_norms():
    """Повтор статьи не меняет порядок узлов и не удаляет другие статьи."""
    text = (
        "Статью 1259 дополнить пунктом 1.\n"
        "Статью 1260 изменить.\n"
        "Статью 1259 дополнить пунктом 2."
    )
    graph = extract_graph("demo.txt", text.encode("utf-8"))
    assert [n["id"] for n in graph["nodes"] if n["type"] == "norm"] == [
        "norm:1259",
        "norm:1260",
    ]
    assert len(graph["records"]) == len(graph["edges"]) == 3
    assert validate_graph(graph) is graph


def test_validation_keeps_parallel_edges_and_does_not_modify_graph():
    """Несколько связей между теми же узлами допустимы; вход не изменяется."""
    graph = _graph()
    original = copy.deepcopy(graph)
    assert validate_graph(graph) is graph
    assert graph == original


@pytest.mark.parametrize(
    "graph",
    [
        {"nodes": [], "edges": []},
        {"nodes": [{"id": "norm:1259"}], "edges": []},
        {
            "nodes": [{"id": "norm:1259"}],
            "edges": [{"from": "norm:1259", "to": "norm:1259"}],
        },
    ],
)
def test_validation_accepts_empty_graph_isolated_node_and_self_reference(graph):
    """Валидатор не вводит ограничений на количество узлов или циклы."""
    assert validate_graph(graph) is graph


def test_validation_reports_duplicate_node_and_first_occurrence():
    """Даже одинаковые узлы с одним ID отклоняются с обоими индексами."""
    graph = _graph()
    graph["nodes"].append(dict(graph["nodes"][1]))
    with pytest.raises(GraphValidationError) as error:
        validate_graph(graph)
    assert "nodes[2].id" in str(error.value)
    assert "nodes[1].id" in str(error.value)
    assert "повтор" in str(error.value)


@pytest.mark.parametrize("field", ["from", "to"])
def test_validation_reports_dangling_reference_in_later_edge(field):
    """Ошибка указывает конкретную связь и конец, для которого нет узла."""
    graph = _graph()
    graph["edges"][1][field] = "norm:missing"
    with pytest.raises(GraphValidationError) as error:
        validate_graph(graph)
    assert f"edges[1].{field}" in str(error.value)
    assert "norm:missing" in str(error.value)


@pytest.mark.parametrize(
    "graph, path",
    [
        ([], "граф"),
        ({}, "nodes"),
        ({"nodes": {}, "edges": []}, "nodes"),
        ({"nodes": [], "edges": {}}, "edges"),
        ({"nodes": [None], "edges": []}, "nodes[0]"),
        ({"nodes": [{}], "edges": []}, "nodes[0].id"),
        ({"nodes": [{"id": []}], "edges": []}, "nodes[0].id"),
        ({"nodes": [{"id": "  "}], "edges": []}, "nodes[0].id"),
        ({"nodes": [], "edges": [None]}, "edges[0]"),
    ],
)
def test_validation_reports_invalid_structure_without_type_errors(graph, path):
    """Неверные типы и пропущенные поля дают ошибку с путём записи."""
    with pytest.raises(GraphValidationError) as error:
        validate_graph(graph)
    assert path in str(error.value)


@pytest.mark.parametrize("field", ["from", "to"])
@pytest.mark.parametrize("value", [None, [], " "])
def test_validation_reports_invalid_reference_type(field, value):
    """Отсутствующие, пустые и нестроковые ссылки отклоняются явно."""
    graph = _graph()
    graph["edges"][0][field] = value
    with pytest.raises(GraphValidationError) as error:
        validate_graph(graph)
    assert f"edges[0].{field}" in str(error.value)


def test_cli_accepts_extracted_graph_with_bom(tmp_path):
    """CLI читает реальный результат извлечения в UTF-8 с BOM."""
    graph = extract_graph("demo.txt", "Статью 1259 изменить.".encode("utf-8"))
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8-sig")
    proc = _run_cli(["validate-graph", str(path)])
    assert proc.returncode == 0, proc.stderr
    assert "узлов: 2" in proc.stdout
    assert "связей: 1" in proc.stdout
    assert "эксперт" in proc.stdout


@pytest.mark.parametrize(
    "kind, path", [("duplicate", "nodes[2].id"), ("dangling", "edges[1].to")]
)
def test_cli_rejects_invalid_graph_with_concrete_location(tmp_path, kind, path):
    """Невалидный граф даёт код 1 и понятную причину без traceback."""
    graph = _graph()
    if kind == "duplicate":
        graph["nodes"].append(dict(graph["nodes"][1]))
    else:
        graph["edges"][1]["to"] = "norm:missing"
    source = tmp_path / "graph.json"
    source.write_text(json.dumps(graph), encoding="utf-8")
    proc = _run_cli(["validate-graph", str(source)])
    assert proc.returncode == 1
    assert path in proc.stderr
    assert "Traceback" not in proc.stderr
    assert proc.stdout == ""


@pytest.mark.parametrize(
    "content", [b'{"nodes":[],"nodes":[],"edges":[]}', b'{"nodes":', b"\xff"]
)
def test_cli_handles_invalid_json_and_encoding(tmp_path, content):
    """Неоднозначный JSON, синтаксические ошибки и неверная кодировка отклоняются."""
    path = tmp_path / "graph.json"
    path.write_bytes(content)
    proc = _run_cli(["validate-graph", str(path)])
    assert proc.returncode == 1
    assert "ОШИБКА" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert proc.stdout == ""


def test_cli_error_does_not_disclose_secret_node_id(tmp_path):
    """Диагностика с ID проходит через общий Redactor."""
    token = "hf_" + "A1b2" * 8
    graph = {"nodes": [{"id": token}, {"id": token}], "edges": []}
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    proc = _run_cli(["validate-graph", str(path)])
    assert proc.returncode == 1
    assert token not in proc.stderr
    assert "[REDACTED]" in proc.stderr
    assert "nodes[1].id" in proc.stderr


def test_cli_file_error_is_redacted(tmp_path):
    """Ошибка чтения файла не раскрывает email из его имени."""
    source = tmp_path / "expert@example.com.json"
    proc = _run_cli(["validate-graph", str(source)])
    assert proc.returncode == 1
    assert "expert@example.com" not in proc.stderr
    assert "[REDACTED]" in proc.stderr
    assert "Traceback" not in proc.stderr
