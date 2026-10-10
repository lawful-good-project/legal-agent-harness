"""Ядро harness: валидация кейсов, событий, повторов и редакция секретов.

Только стандартная библиотека. Сеть не используется.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

# События прогона. Порядок отражает жизненный цикл кейса.
EVENTS = (
    "task",
    "agent_answer",
    "validator_feedback",
    "expert_review",
    "final",
)

# Финальный вердикт всегда требует одобрения эксперта.
FINAL_ALWAYS_NEEDS_EXPERT_APPROVAL = True

DEFAULT_MAX_ROUNDS = 3

# Обязательные поля источника.
SOURCE_REQUIRED_FIELDS = (
    "authority",
    "jurisdiction",
    "doc_type",
    "identifier",
    "coordinate",
)

AMENDMENT_PREDICATES = frozenset(
    {
        "изменена",
        "дополнена",
        "введена",
        "изложена_в_новой_редакции",
        "признана_утратившей_силу",
        "исключена",
        "уточнена",
    }
)

# Обязательные поля ожидания.
EXPECTED_REQUIRED_FIELDS = (
    "norm_id",
    "predicate",
    "subject",
    "object",
    "confidence",
)

# Допустимые границы (exact keys) для source/expected.
SOURCE_ALLOWED_FIELDS = frozenset(SOURCE_REQUIRED_FIELDS + ("effective_date",))
EXPECTED_ALLOWED_FIELDS = frozenset(
    EXPECTED_REQUIRED_FIELDS + ("effective_from", "effective_to", "change_description")
)

# Секреты: ключи, которые должны быть вырезаны из событий.
_SECRET_KEY_RE = re.compile(
    r"(token|secret|api[_-]?key|password|passwd|credential|authorization|bearer)",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Длинные токен-подобные строки (>=24 символов, смешанный алфавит).
_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-\.]{24,}\b")

REDACTION_PLACEHOLDER = "[REDACTED]"


class HarnessError(Exception):
    """Базовая ошибка harness."""


class CaseValidationError(HarnessError):
    """Кейс не проходит валидацию схемы."""


class ResponseValidationError(HarnessError):
    """Ответ агента не проходит валидацию ожидаемой структуры."""


def _is_str(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def validate_case(case: Any) -> Dict[str, Any]:
    """Проверяет кейс и возвращает его же.

    Требует точного набора полей source и expected (без пропусков и лишних).
    """
    if not isinstance(case, dict):
        raise CaseValidationError("кейс должен быть JSON-объектом")
    case_id = case.get("case_id")
    if not _is_str(case_id):
        raise CaseValidationError("case_id обязателен и должен быть непустой строкой")

    source = case.get("source")
    if not isinstance(source, dict):
        raise CaseValidationError(
            f"[{case_id}] source обязателен и должен быть объектом"
        )
    missing_src = [f for f in SOURCE_REQUIRED_FIELDS if not _is_str(source.get(f))]
    if missing_src:
        raise CaseValidationError(
            f"[{case_id}] source: обязательные поля отсутствуют/пусты: {missing_src}"
        )
    extra_src = sorted(set(source) - SOURCE_ALLOWED_FIELDS)
    if extra_src:
        raise CaseValidationError(f"[{case_id}] source: недопустимые поля: {extra_src}")

    if "effective_date" in source:
        date = source["effective_date"]
        if date is not None and (
            not isinstance(date, str)
            or not re.fullmatch(r"[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?", date)
        ):
            raise CaseValidationError(
                f"[{case_id}] source.effective_date имеет неверный формат"
            )

    expected = case.get("expected")
    if not isinstance(expected, dict):
        raise CaseValidationError(
            f"[{case_id}] expected обязателен и должен быть объектом"
        )
    missing_exp = [
        f
        for f in EXPECTED_REQUIRED_FIELDS
        if (expected.get(f) is None or expected.get(f) == "")
    ]
    if missing_exp:
        raise CaseValidationError(
            f"[{case_id}] expected: обязательные поля отсутствуют/пусты: {missing_exp}"
        )
    extra_exp = sorted(set(expected) - EXPECTED_ALLOWED_FIELDS)
    if extra_exp:
        raise CaseValidationError(
            f"[{case_id}] expected: недопустимые поля: {extra_exp}"
        )

    if source.get("doc_type") == "amending_act":
        predicate = expected.get("predicate")
        if predicate not in AMENDMENT_PREDICATES:
            raise CaseValidationError(
                f"[{case_id}] expected.predicate недопустим для amending_act: {predicate!r}"
            )

    conf = expected.get("confidence")
    try:
        conf_val = float(conf)
    except (TypeError, ValueError):
        raise CaseValidationError(f"[{case_id}] expected.confidence должен быть числом")
    if not 0.0 <= conf_val <= 1.0:
        raise CaseValidationError(
            f"[{case_id}] expected.confidence должен быть в диапазоне [0, 1]"
        )
    return case


def validate_response(response: Any) -> Dict[str, Any]:
    """Проверяет ответ агента: должен содержать expected-структуру."""
    if not isinstance(response, dict):
        raise ResponseValidationError("ответ должен быть JSON-объектом")
    if "status" in response and response.get("status") not in ("ok", "needs_revision"):
        raise ResponseValidationError(
            f"недопустимый status: {response.get('status')!r}"
        )
    expected = response.get("expected")
    if not isinstance(expected, dict):
        raise ResponseValidationError("ответ должен содержать объект expected")
    missing = [
        f
        for f in EXPECTED_REQUIRED_FIELDS
        if (expected.get(f) is None or expected.get(f) == "")
    ]
    if missing:
        raise ResponseValidationError(
            f"ответ.expected: обязательные поля отсутствуют/пусты: {missing}"
        )
    return response


def matches_expectation(case: Dict[str, Any], response: Dict[str, Any]) -> bool:
    """Точное сравнение полей expected кейса и ответа агента."""
    want = case.get("expected", {})
    got = response.get("expected", {})
    for key, value in want.items():
        if key == "confidence":
            try:
                if abs(float(value) - float(got.get(key))) > 1e-9:
                    return False
            except (TypeError, ValueError):
                return False
        elif got.get(key) != value:
            return False
    return True


def validate_temporal_consistency(
    case: Dict[str, Any], response: Dict[str, Any]
) -> List[str]:
    """Проверяет базовую хронологию изменения нормы.

    Это проверка здравого смысла, а не юридическое заключение: отсутствие даты
    не считается ошибкой, но противоречивые даты не могут быть одобрены.
    """
    source_date = case.get("source", {}).get("effective_date")
    expected = response.get("expected", {})
    effective_from = expected.get("effective_from")
    effective_to = expected.get("effective_to")
    errors: List[str] = []
    if source_date and effective_from and effective_from < source_date:
        errors.append(
            f"effective_from {effective_from} раньше даты источника {source_date}"
        )
    if effective_from and effective_to and effective_to < effective_from:
        errors.append(
            f"effective_to {effective_to} раньше effective_from {effective_from}"
        )
    return errors


def redact(value: Any) -> Any:
    """Рекурсивно вырезает токены/секреты/email из структур событий."""
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _SECRET_KEY_RE.search(key):
                out[key] = REDACTION_PLACEHOLDER
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = _EMAIL_RE.sub(REDACTION_PLACEHOLDER, value)
        value = _TOKEN_RE.sub(REDACTION_PLACEHOLDER, value)
        return value
    return value


class Redactor:
    """Настраиваемый редактор секретов для событий прогона."""

    def __init__(self, extra_patterns: Optional[Iterable[str]] = None) -> None:
        self._patterns = [re.compile(p) for p in (extra_patterns or [])]

    def scrub(self, value: Any) -> Any:
        value = redact(value)
        if self._patterns:
            value = _apply_extra(value, self._patterns)
        return value


def _apply_extra(value: Any, patterns: List[re.Pattern]) -> Any:
    if isinstance(value, dict):
        return {k: _apply_extra(v, patterns) for k, v in value.items()}
    if isinstance(value, list):
        return [_apply_extra(v, patterns) for v in value]
    if isinstance(value, str):
        for pattern in patterns:
            value = pattern.sub(REDACTION_PLACEHOLDER, value)
        return value
    return value


@dataclass
class RunEvent:
    """Одно событие прогона кейса."""

    case_id: str
    event: str
    round: int
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, redactor: Optional[Redactor] = None) -> Dict[str, Any]:
        if self.event not in EVENTS:
            raise HarnessError(f"неизвестный тип события: {self.event!r}")
        data = {
            "case_id": self.case_id,
            "event": self.event,
            "round": self.round,
            "payload": self.payload,
        }
        redactor = redactor or Redactor()
        return redactor.scrub(data)


@dataclass
class RunResult:
    """Результат прогона одного кейса."""

    case_id: str
    status: str
    rounds: int
    approved: bool
    needs_expert_approval: bool
    events: List[Dict[str, Any]] = field(default_factory=list)
    output: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "status": self.status,
            "rounds": self.rounds,
            "approved": self.approved,
            "needs_expert_approval": self.needs_expert_approval,
            "events": self.events,
            "output": self.output,
        }


def run_case(
    case: Dict[str, Any],
    adapter: Any,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    redactor: Optional[Redactor] = None,
) -> RunResult:
    """Прогоняет один кейс через адаптер с ограниченным числом повторов.

    Всегда эмитит события task -> agent_answer -> validator_feedback
    -> expert_review -> final. final всегда требует одобрения эксперта.
    """
    validate_case(case)
    if max_rounds < 1:
        raise HarnessError("max_rounds должен быть >= 1")
    redactor = redactor or Redactor()
    case_id = case["case_id"]

    events: List[RunEvent] = []

    def emit(event: str, round_no: int, payload: Dict[str, Any]) -> None:
        events.append(RunEvent(case_id, event, round_no, payload))

    emit("task", 0, {"source": case["source"], "expected": case["expected"]})

    attempts = 0
    last_response: Optional[Dict[str, Any]] = None
    last_error: Optional[str] = None
    matched = False

    for round_no in range(1, max_rounds + 1):
        attempts = round_no
        try:
            raw = adapter.respond(case)
        except Exception as exc:  # адаптер обязан изолировать свои ошибки
            last_error = f"ошибка адаптера: {exc}"
            emit("agent_answer", round_no, {"error": last_error})
            emit("validator_feedback", round_no, {"ok": False, "reason": last_error})
            continue

        emit("agent_answer", round_no, {"response": raw})

        try:
            response = validate_response(raw)
        except ResponseValidationError as exc:
            last_error = str(exc)
            emit("validator_feedback", round_no, {"ok": False, "reason": last_error})
            continue

        last_response = response
        matched = matches_expectation(case, response)
        temporal_errors = validate_temporal_consistency(case, response)
        if temporal_errors:
            matched = False
        emit(
            "validator_feedback",
            round_no,
            {
                "ok": matched,
                "reason": (
                    "совпадение"
                    if matched
                    else ("; ".join(temporal_errors) if temporal_errors else "расхождение с ожиданием")
                ),
            },
        )
        if matched:
            break

    approved = bool(matched)
    status = "approved" if approved else "needs_expert_approval"
    emit(
        "expert_review",
        0,
        {
            "approved": approved,
            "attempts": attempts,
            "last_error": last_error,
        },
    )
    emit(
        "final",
        0,
        {
            "status": status,
            "needs_expert_approval": True,
            "attempts": attempts,
            "response": last_response,
        },
    )

    return RunResult(
        case_id=case_id,
        status=status,
        rounds=attempts,
        approved=approved,
        needs_expert_approval=True,
        events=[ev.to_dict(redactor) for ev in events],
        output=last_response or {},
    )


def load_cases(path: str) -> List[Dict[str, Any]]:
    """Загружает и валидирует все кейсы из JSONL-файла.

    ``case_id`` должен быть уникален в пределах файла. Дубликат — ошибка:
    иначе ``replay`` подставит один и тот же ответ в два разных кейса, а в
    отчёте прогона их события невозможно будет различить.
    """
    cases: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for line_no, obj in enumerate(_iter_jsonl(path), start=1):
        try:
            validate_case(obj)
        except CaseValidationError as exc:
            raise CaseValidationError(f"{path}:{line_no}: {exc}") from exc
        case_id = obj["case_id"]
        if case_id in seen:
            raise CaseValidationError(
                f"{path}:{line_no}: дублирующийся case_id {case_id!r} "
                f"(впервые встречается в записи {seen[case_id]})"
            )
        seen[case_id] = line_no
        cases.append(obj)
    return cases


def load_responses(path: str) -> Dict[str, Dict[str, Any]]:
    """Загружает карту case_id -> response из JSONL-файла.

    ``case_id`` должен быть уникален. Раньше второй ответ с тем же ``case_id``
    молча перезаписывал первый, и было неизвестно, какой из ответов агента
    попал в прогон; теперь это ошибка валидации.
    """
    responses: Dict[str, Dict[str, Any]] = {}
    seen: Dict[str, int] = {}
    for line_no, obj in enumerate(_iter_jsonl(path), start=1):
        if not isinstance(obj, dict) or not _is_str(obj.get("case_id")):
            raise ResponseValidationError(f"{path}:{line_no}: требуется case_id")
        case_id = obj["case_id"]
        if case_id in seen:
            raise ResponseValidationError(
                f"{path}:{line_no}: дублирующийся case_id {case_id!r} "
                f"(впервые встречается в записи {seen[case_id]})"
            )
        seen[case_id] = line_no
        responses[case_id] = obj
    return responses


def _iter_jsonl(path: str) -> Iterator[Any]:
    with open(path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise HarnessError(
                    f"{path}:{line_no}: некорректный JSON: {exc}"
                ) from exc
