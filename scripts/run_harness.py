#!/usr/bin/env python
"""CLI калибровочного harness (stdlib-only).

Команды:
  validate-cases  проверить JSONL с кейсами
  run             прогнать кейсы через адаптер
  replay          прогнать кейсы через заготовленные ответы
  report          вывести отчёт по прогону

Коды возврата:
  0 — успех; 1 — ошибка; 2 — ошибка аргументов (argparse).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Разрешаем запуск без установки пакета (src-layout).
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# Русский вывод в CLI: не падать на консолях с cp1251.
for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

from legal_agent_harness import (  # noqa: E402
    CaseValidationError,
    HarnessError,
    Redactor,
    load_cases,
    load_responses,
    run_case,
    validate_case,
)
from legal_agent_harness.adapters import (  # noqa: E402
    AdapterError,
    get_adapter,
)

EXIT_OK = 0
EXIT_ERROR = 1

EPILOG = (
    "Примеры:\n"
    "  python scripts/run_harness.py validate-cases examples/cases.jsonl\n"
    "  python scripts/run_harness.py replay --cases examples/cases.jsonl "
    "--responses examples/responses.jsonl\n"
    "  python scripts/run_harness.py run --cases examples/cases.jsonl "
    '--command "python agent.py" --timeout 10 --max-output-bytes 200000\n'
)


def cmd_validate_cases(args: argparse.Namespace) -> int:
    """Проверяет JSONL с кейсами и печатает число валидных кейсов."""
    try:
        cases = load_cases(args.cases)
    except (CaseValidationError, HarnessError) as exc:
        print(f"ОШИБКА валидации кейсов: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"OK: {len(cases)} кейсов прошли валидацию")
    return EXIT_OK


def _run_all(cases, adapter, args) -> int:
    results = []
    for case in cases:
        result = run_case(
            case,
            adapter,
            max_rounds=args.max_rounds,
            redactor=Redactor(),
        )
        results.append(result)
    payload = [r.to_dict() for r in results]
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    approved = sum(1 for r in results if r.approved)
    print(
        f"\nИтого: {approved}/{len(results)} одобрено, "
        f"все требуют экспертного одобрения.",
        file=sys.stderr,
    )
    return EXIT_OK


def cmd_replay(args: argparse.Namespace) -> int:
    """Прогоняет кейсы через заготовленные JSON-ответы."""
    try:
        cases = load_cases(args.cases)
        responses = load_responses(args.responses)
        adapter = get_adapter("replay", responses=responses)
    except (CaseValidationError, HarnessError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return _run_all(cases, adapter, args)


def cmd_run(args: argparse.Namespace) -> int:
    """Прогоняет кейсы через локальный процесс (без сети)."""
    try:
        cases = load_cases(args.cases)
        adapter = get_adapter(
            "local-process",
            command=args.command,
            timeout=args.timeout,
            max_output_bytes=args.max_output_bytes,
            use_shell=False,
        )
    except (CaseValidationError, HarnessError, AdapterError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return _run_all(cases, adapter, args)


def cmd_report(args: argparse.Namespace) -> int:
    """Печатает сводный отчёт по JSON-файлу прогона."""
    try:
        with open(args.input, "r", encoding="utf-8") as fh:
            results = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ОШИБКА чтения отчёта: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if not isinstance(results, list):
        print("ОШИБКА: отчёт должен быть JSON-массивом", file=sys.stderr)
        return EXIT_ERROR

    total = len(results)
    approved = sum(1 for r in results if r.get("approved"))
    pending = sum(1 for r in results if r.get("needs_expert_approval"))
    print("Отчёт о калибровке")
    print(f"  кейсов:              {total}")
    print(f"  одобрено:            {approved}")
    print(f"  ждут эксперта:       {pending}")
    print(
        f"  среднее число раундов: "
        f"{(sum(r.get('rounds', 0) for r in results) / total):.2f}"
        if total
        else "  среднее число раундов: 0.00"
    )
    for r in results:
        mark = "OK " if r.get("approved") else "!! "
        print(
            f"  {mark}{r.get('case_id')} status={r.get('status')} "
            f"rounds={r.get('rounds')}"
        )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_harness.py",
        description="Калибровочный harness для правовых агентов (только stdlib).",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="КОМАНДА")

    p_validate = sub.add_parser(
        "validate-cases",
        help="проверить JSONL с кейсами",
        description="Проверка кейсов.",
    )
    p_validate.add_argument("cases", help="путь к JSONL с кейсами")
    p_validate.set_defaults(func=cmd_validate_cases)

    p_run = sub.add_parser(
        "run",
        help="прогнать кейсы через локальный процесс",
        description="Прогон через локальный процесс (без сети) с таймаутом и лимитом вывода.",
    )
    p_run.add_argument("--cases", required=True, help="путь к JSONL с кейсами")
    p_run.add_argument("--command", required=True, help="локальная команда агента")
    p_run.add_argument("--timeout", type=float, default=20.0, help="таймаут в секундах")
    p_run.add_argument(
        "--max-output-bytes",
        type=int,
        default=1_000_000,
        help="лимит размера вывода в байтах",
    )
    p_run.add_argument(
        "--max-rounds", type=int, default=3, help="максимум раундов повтора"
    )
    p_run.add_argument("--out", help="файл для сохранения JSON-результатов")
    p_run.set_defaults(func=cmd_run)

    p_replay = sub.add_parser(
        "replay",
        help="прогнать кейсы через заготовленные ответы",
        description="Прогон через ReplayAdapter по JSONL с ответами.",
    )
    p_replay.add_argument("--cases", required=True, help="путь к JSONL с кейсами")
    p_replay.add_argument("--responses", required=True, help="путь к JSONL с ответами")
    p_replay.add_argument(
        "--max-rounds", type=int, default=3, help="максимум раундов повтора"
    )
    p_replay.add_argument("--out", help="файл для сохранения JSON-результатов")
    p_replay.set_defaults(func=cmd_replay)

    p_report = sub.add_parser(
        "report", help="вывести отчёт по прогону", description="Сводный отчёт."
    )
    p_report.add_argument("input", help="JSON-файл с результатами прогона")
    p_report.set_defaults(func=cmd_report)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_ERROR
    try:
        return args.func(args)
    except (HarnessError, AdapterError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
