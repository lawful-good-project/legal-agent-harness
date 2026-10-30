"""Тесты скрипта автоформатирования. ruff не запускается."""

import contextlib
import importlib.util
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
_spec = importlib.util.spec_from_file_location(
    "format_code", ROOT / "scripts" / "format_code.py"
)
format_code = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(format_code)


def _ruff_args(command):
    assert command[:4] == [sys.executable, "-m", "ruff", "format"]
    return command[4:]


def test_default_paths_are_repository_code():
    args = _ruff_args(format_code.build_command([]))
    assert args == [str(ROOT.resolve() / name) for name in ("src", "scripts", "tests")]


def test_check_and_diff_flags():
    args = _ruff_args(format_code.build_command(["a.py"], check=True, diff=True))
    assert args == ["--check", "--diff", os.path.abspath("a.py")]


def test_user_paths_are_resolved_from_current_directory(tmp_path):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        args = _ruff_args(format_code.build_command(["x.py", "pkg"]))
    finally:
        os.chdir(cwd)
    assert args == [str(tmp_path / "x.py"), str(tmp_path / "pkg")]


def test_missing_ruff_is_reported():
    stderr = io.StringIO()
    find_spec = format_code.importlib.util.find_spec
    format_code.importlib.util.find_spec = lambda name: None
    try:
        with contextlib.redirect_stderr(stderr):
            code = format_code.main(["--check"])
    finally:
        format_code.importlib.util.find_spec = find_spec
    assert code == format_code.EXIT_NO_RUFF
    message = 'ruff не установлен. Установите: pip install -e ".[dev]"'
    assert message in stderr.getvalue()


def test_runs_ruff_with_built_command():
    calls = []
    find_spec, call = format_code.importlib.util.find_spec, format_code.subprocess.call
    format_code.importlib.util.find_spec = lambda name: object()
    format_code.subprocess.call = lambda command, cwd: calls.append((command, cwd)) or 1
    try:
        code = format_code.main(["--check", "x.py"])
    finally:
        format_code.importlib.util.find_spec = find_spec
        format_code.subprocess.call = call
    assert code == 1
    expected = format_code.build_command(["x.py"], check=True)
    assert calls == [(expected, format_code.ROOT)]
