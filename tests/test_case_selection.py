"""Выбор case_id для run и replay: порядок, повторы и ошибки. Сеть не используется."""

from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from legal_agent_harness import load_cases  # noqa: E402


@pytest.fixture
def inputs(tmp_path):
    """Набор amendment-кейсов и его копия во временном JSONL-файле."""
    cases = load_cases(str(ROOT / "examples" / "amendments.jsonl"))
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases),
        encoding="utf-8",
    )
    return cases, cases_path


def _command(mode, cases, cases_path, tmp_path):
    args = [mode, "--cases", str(cases_path)]
    if mode == "replay":
        responses = tmp_path / "responses.jsonl"
        responses.write_text(
            "".join(
                json.dumps(
                    {
                        "case_id": c["case_id"],
                        "response": {"expected": c["expected"]},
                    }
                )
                + "\n"
                for c in cases
            ),
            encoding="utf-8",
        )
        return args + ["--responses", str(responses)]
    agent = tmp_path / "agent.py"
    answers = {c["case_id"]: {"expected": c["expected"]} for c in cases}
    agent.write_text(
        "import json, pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text('запущен', encoding='utf-8')\n"
        "task = json.load(sys.stdin)\n"
        f"answers = {answers!r}\n"
        "print(json.dumps(answers[task['case_id']]))\n",
        encoding="utf-8",
    )
    command = shlex.join([sys.executable, str(agent), str(tmp_path / "started")])
    return args + ["--command", command]


def _run_cli(args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_harness.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@pytest.mark.parametrize("mode", ["run", "replay"])
def test_cli_runs_only_selected_case(inputs, tmp_path, mode):
    """Выбранный кейс совпадает с эталоном и по-прежнему требует эксперта."""
    cases, path = inputs
    selected = cases[1]
    # Ответов на остальные кейсы намеренно нет.
    args = _command(mode, [selected], path, tmp_path)

    proc = _run_cli(args + ["--case-id", selected["case_id"]])

    assert proc.returncode == 0, proc.stderr
    (result,) = json.loads(proc.stdout)
    assert result["case_id"] == selected["case_id"]
    assert result["approved"] is True
    assert result["needs_expert_approval"] is True
    assert result["events"][-1]["payload"]["needs_expert_approval"] is True


@pytest.mark.parametrize("mode", ["run", "replay"])
def test_cli_selection_preserves_file_order_without_repeated_runs(
    inputs, tmp_path, mode
):
    """Порядок берётся из файла; повтор ID не создаёт повторный прогон."""
    cases, path = inputs
    args = _command(mode, cases, path, tmp_path)
    out = tmp_path / "result.json"

    proc = _run_cli(
        args
        + [
            "--case-id",
            cases[2]["case_id"],
            "--case-id",
            cases[0]["case_id"],
            "--case-id",
            cases[2]["case_id"],
            "--out",
            str(out),
        ]
    )

    assert proc.returncode == 0, proc.stderr
    results = json.loads(proc.stdout)
    assert [r["case_id"] for r in results] == [cases[0]["case_id"], cases[2]["case_id"]]
    assert json.loads(out.read_text(encoding="utf-8")) == results
    assert all(r["approved"] and r["needs_expert_approval"] for r in results)


@pytest.mark.parametrize("mode", ["run", "replay"])
def test_unknown_case_id_fails_before_running_any_case(inputs, tmp_path, mode):
    """Неизвестный ID даёт код 1 без запуска агента и записи отчёта."""
    cases, path = inputs
    args = _command(mode, cases, path, tmp_path)
    out = tmp_path / "result.json"

    proc = _run_cli(
        args
        + [
            "--case-id",
            cases[0]["case_id"],
            "--case-id",
            "unknown-case",
            "--out",
            str(out),
        ]
    )

    assert proc.returncode == 1
    assert "неизвестные case_id" in proc.stderr
    assert "unknown-case" in proc.stderr
    assert "--case-id" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert proc.stdout == ""
    assert not out.exists()
    assert not (tmp_path / "started").exists()
