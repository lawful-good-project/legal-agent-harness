"""Повторяющиеся JSON-ключи в ответе локального агента. Сеть не используется."""

from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from legal_agent_harness import (  # noqa: E402
    AdapterError,
    LocalProcessAdapter,
    load_cases,
    run_case,
)


def _case():
    return load_cases(str(ROOT / "examples" / "amendments.jsonl"))[0]


def _adapter(stdout):
    return LocalProcessAdapter(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.argv[1])", stdout]
    )


def _duplicate_answer(case, field, value):
    # Ошибочное значение стоит первым: обычный json.loads оставит эталонное.
    pair = json.dumps(field) + ":" + json.dumps(value, ensure_ascii=False)
    expected = json.dumps(case["expected"], ensure_ascii=False)
    return '{"expected":{' + pair + "," + expected[1:] + "}"


@pytest.mark.parametrize(
    "field, value",
    [
        ("norm_id", "другая-норма"),
        ("predicate", "исключена"),
        ("effective_from", "2099-01-01"),
        ("confidence", 0.1),
    ],
)
def test_local_process_rejects_conflicting_fields(field, value):
    """Конфликтующие реквизиты поправки отклоняются до сравнения с эталоном."""
    case = _case()
    with pytest.raises(AdapterError, match=f"повторяющийся ключ '{field}'"):
        _adapter(_duplicate_answer(case, field, value)).respond(case)


@pytest.mark.parametrize(
    "stdout, key",
    [
        ('{"expected":{},"expected":{}}', "expected"),
        (
            '{"expected":{"predicate":"изменена","predicate":"изменена"}}',
            "predicate",
        ),
        (
            r'{"expected":{"predicate":"исключена","\u0070redicate":"изменена"}}',
            "predicate",
        ),
        ('{"notes":[{"coordinate":"п. 1","coordinate":"п. 2"}]}', "coordinate"),
    ],
)
def test_local_process_rejects_duplicates_in_any_json_object(stdout, key):
    """Дубли ключей запрещены во вложенных объектах, в том числе внутри списков."""
    with pytest.raises(AdapterError, match=f"повторяющийся ключ '{key}'"):
        _adapter(stdout).respond(_case())


def test_conflicting_amendment_never_matches_and_requires_expert():
    """Неоднозначный ответ не совпадает с эталоном за отведённое число раундов."""
    case = _case()
    adapter = _adapter(_duplicate_answer(case, "predicate", "исключена"))

    result = run_case(case, adapter, max_rounds=2)

    assert result.rounds == 2
    assert result.approved is False
    assert result.status == "needs_expert_approval"
    assert result.needs_expert_approval is True
    assert result.output == {}
    feedback = [
        e["payload"] for e in result.events if e["event"] == "validator_feedback"
    ]
    assert len(feedback) == 2
    assert all(not f["ok"] and "predicate" in f["reason"] for f in feedback)
    assert result.events[-1]["payload"]["needs_expert_approval"] is True


def test_correct_answer_after_duplicate_key_can_match():
    """После ошибки JSON можно повторить попытку; экспертная проверка сохраняется."""
    case = _case()
    correct = {"expected": case["expected"]}
    adapters = iter(
        [
            _adapter(_duplicate_answer(case, "predicate", "исключена")),
            _adapter(json.dumps(correct, ensure_ascii=False)),
        ]
    )

    class SequentialAdapter:
        """Вызывает локальные процессы с ошибочным и исправленным ответами."""

        def respond(self, task):
            return next(adapters).respond(task)

    result = run_case(case, SequentialAdapter(), max_rounds=2)

    assert result.rounds == 2
    assert result.approved is True
    assert result.output == correct
    assert result.needs_expert_approval is True
    assert result.events[-1]["payload"]["needs_expert_approval"] is True


def test_local_process_accepts_unambiguous_json():
    """Корректный JSON-ответ с кириллицей возвращается без изменений."""
    case = _case()
    response = {"expected": case["expected"], "notes": [{"text": "проверить норму"}]}
    assert _adapter(json.dumps(response, ensure_ascii=False)).respond(case) == response


def test_cli_reports_duplicate_key_without_false_match(tmp_path):
    """CLI сохраняет причину отклонения в stdout и --out без traceback."""
    case = _case()
    cases = tmp_path / "cases.jsonl"
    cases.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    agent = tmp_path / "agent.py"
    stdout = _duplicate_answer(case, "predicate", "исключена")
    agent.write_text("print(" + repr(stdout) + ")\n", encoding="utf-8")
    out = tmp_path / "result.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_harness.py"),
            "run",
            "--cases",
            str(cases),
            "--command",
            shlex.join([sys.executable, str(agent)]),
            "--max-rounds",
            "1",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "Traceback" not in proc.stderr
    (result,) = json.loads(proc.stdout)
    assert json.loads(out.read_text(encoding="utf-8")) == [result]
    assert result["approved"] is False
    assert result["needs_expert_approval"] is True
    feedback = next(
        e["payload"] for e in result["events"] if e["event"] == "validator_feedback"
    )
    assert "повторяющийся ключ 'predicate'" in feedback["reason"]
