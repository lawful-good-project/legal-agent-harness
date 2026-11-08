"""Безопасность публикуемого отчёта прогона (P41). Сеть не используется.

Regression: раньше traceback упавшего локального агента попадал в отчёт
(`--out`) вместе с абсолютным путём к файлу агента, например
``/Users/<имя>/agent.py``, и раскрывал имя пользователя и структуру диска.
Теперь `Redactor` заменяет абсолютные локальные пути на ``[REDACTED]``,
а юридические метаданные кейса и ответа сохраняются без изменений.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from legal_agent_harness import load_cases, redact, run_case  # noqa: E402
from legal_agent_harness.adapters import AdapterError  # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples")


def _run_cli(args):
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "run_harness.py"), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        check=False,
    )


def _case():
    return load_cases(os.path.join(EXAMPLES, "cases.jsonl"))[0]


class _CrashingAdapter:
    """Адаптер, чья ошибка содержит traceback с локальными путями."""

    def respond(self, case):
        raise AdapterError(
            "процесс завершился с кодом 1: Traceback (most recent call last):\n"
            '  File "/Users/ivanov/projects/agent.py", line 2\n'
            '  File "C:\\Users\\ivanov\\agent.py", line 7\n'
            "RuntimeError: см. /home/ivanov/run.log, пишите ivanov@example.com"
        )


def test_regression_traceback_paths_not_in_result():
    result = run_case(_case(), _CrashingAdapter(), max_rounds=1)
    blob = json.dumps(result.to_dict(), ensure_ascii=False)
    assert "ivanov" not in blob
    # Диагностика без путей сохраняется: эксперт видит, что агент упал.
    assert "процесс завершился с кодом 1" in blob
    assert "RuntimeError" in blob


@pytest.mark.parametrize(
    "path",
    [
        "/Users/ivanov/agent.py",
        "/home/ivanov/agent.py",
        "/private/var/folders/xy/ivanov/T/agent.py",
        "/tmp/ivanov/agent.py",
        "C:\\Users\\ivanov\\agent.py",
        "~/ivanov/agent.py",
    ],
)
def test_redact_local_paths(path):
    cleaned = redact(f'File "{path}", line 1')
    assert "ivanov" not in cleaned
    assert cleaned == 'File "[REDACTED]", line 1'


@pytest.mark.parametrize(
    "text,expected",
    [
        ("см. /home/ivanov/run.log, пишите", "см. [REDACTED], пишите"),
        ("(лог: /home/ivanov/run.log)", "(лог: [REDACTED])"),
        ("лог в /tmp/ivanov/run.log.", "лог в [REDACTED]."),
        ("папка ~/ivanov/; дальше", "папка [REDACTED]; дальше"),
    ],
)
def test_redact_keeps_punctuation_after_path(text, expected):
    assert redact(text) == expected


@pytest.mark.parametrize(
    "value",
    [
        "гк-рф/ст.1252",
        "document/article/12",
        "35 U.S.C. § 102",
        "ФЗ от 01.07.2017 № 147-ФЗ",
        "ст. 2 п. 3",
        "(a)(1)",
        "https://example.org/home/doc",
    ],
)
def test_redact_keeps_legal_identifiers(value):
    assert redact(value) == value


def test_cli_report_of_failed_agent_has_no_local_paths(tmp_path):
    # Короткое имя каталога: длинное вырезал бы _TOKEN_RE, скрыв утечку пути.
    agent = tmp_path / "ivanov" / "agent.py"
    agent.parent.mkdir()
    agent.write_text(
        'raise RuntimeError("агент упал, пишите dev@example.com")\n',
        encoding="utf-8",
    )
    out = tmp_path / "run.json"
    proc = _run_cli(
        [
            "run",
            "--cases",
            os.path.join(EXAMPLES, "cases.jsonl"),
            "--command",
            f"{shlex.quote(sys.executable)} {shlex.quote(str(agent))}",
            "--max-rounds",
            "1",
            "--out",
            str(out),
        ]
    )
    assert proc.returncode == 0, proc.stderr
    report = out.read_text(encoding="utf-8")
    for text in (proc.stdout, report):
        assert "ivanov" not in text
        assert "dev@example.com" not in text
        assert "RuntimeError" in text


@pytest.mark.parametrize(
    "cases_file,responses_file",
    [
        ("cases.jsonl", "responses.jsonl"),
        ("amendments.jsonl", "amendments_responses.jsonl"),
    ],
)
def test_cli_report_keeps_legal_metadata(tmp_path, cases_file, responses_file):
    cases_path = os.path.join(EXAMPLES, cases_file)
    out = tmp_path / "run.json"
    proc = _run_cli(
        [
            "replay",
            "--cases",
            cases_path,
            "--responses",
            os.path.join(EXAMPLES, responses_file),
            "--out",
            str(out),
        ]
    )
    assert proc.returncode == 0, proc.stderr
    results = {r["case_id"]: r for r in json.loads(out.read_text(encoding="utf-8"))}
    for case in load_cases(cases_path):
        result = results[case["case_id"]]
        # Источник и ожидание публикуются без единого вырезанного поля.
        task = next(e for e in result["events"] if e["event"] == "task")
        assert task["payload"]["source"] == case["source"]
        assert result["output"]["expected"] == case["expected"]
        assert result["needs_expert_approval"] is True
        assert "[REDACTED]" not in json.dumps(result, ensure_ascii=False)
