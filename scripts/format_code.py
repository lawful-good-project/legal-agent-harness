#!/usr/bin/env python
"""Автоформатирование кода репозитория через ruff format.

Использование:
  python scripts/format_code.py              отформатировать src, scripts, tests
  python scripts/format_code.py ПУТЬ...      отформатировать указанные файлы/каталоги
  python scripts/format_code.py --check      только проверить, ничего не меняя
  python scripts/format_code.py --diff       показать изменения, ничего не меняя

Коды возврата:
  0 — успех; 1 — есть неотформатированные файлы (--check/--diff) или ошибка ruff;
  2 — ruff не установлен (pip install -e ".[dev]").
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATHS = ("src", "scripts", "tests")

EXIT_NO_RUFF = 2


def build_command(paths, check=False, diff=False):
    """Команда запуска ruff format для указанных путей."""
    command = [sys.executable, "-m", "ruff", "format"]
    if check:
        command.append("--check")
    if diff:
        command.append("--diff")
    if paths:
        command.extend(os.path.abspath(path) for path in paths)
    else:
        command.extend(os.path.join(ROOT, path) for path in DEFAULT_PATHS)
    return command


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="format_code.py",
        description="Автоформатирование кода репозитория (ruff format).",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="ПУТЬ",
        help="файлы или каталоги (по умолчанию: src, scripts, tests)",
    )
    parser.add_argument(
        "--check", action="store_true", help="только проверить, ничего не меняя"
    )
    parser.add_argument(
        "--diff", action="store_true", help="показать изменения, ничего не меняя"
    )
    args = parser.parse_args(argv)

    if importlib.util.find_spec("ruff") is None:
        print(
            'ОШИБКА: ruff не установлен. Установите: pip install -e ".[dev]"',
            file=sys.stderr,
        )
        return EXIT_NO_RUFF

    # Настройки (длина строки, версия Python) берутся из [tool.ruff] в pyproject.toml.
    return subprocess.call(build_command(args.paths, args.check, args.diff), cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
