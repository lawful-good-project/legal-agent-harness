"""Legal agent calibration harness (stdlib-only).

Пакет для калибровки агентов извлечения правовой информации на наборе
экспертно-проверенных кейсов. Все компоненты работают локально, без сети.
"""

from .core import (
    CaseValidationError,
    HarnessError,
    Redactor,
    RunEvent,
    RunResult,
    ResponseValidationError,
    load_cases,
    load_responses,
    redact,
    run_case,
    validate_case,
    validate_response,
    EVENTS,
    FINAL_ALWAYS_NEEDS_EXPERT_APPROVAL,
)
from .adapters import (
    Adapter,
    AdapterError,
    LocalProcessAdapter,
    ReplayAdapter,
    get_adapter,
)

__all__ = [
    "CaseValidationError",
    "HarnessError",
    "Redactor",
    "RunEvent",
    "RunResult",
    "ResponseValidationError",
    "load_cases",
    "load_responses",
    "redact",
    "run_case",
    "validate_case",
    "validate_response",
    "EVENTS",
    "FINAL_ALWAYS_NEEDS_EXPERT_APPROVAL",
    "Adapter",
    "AdapterError",
    "LocalProcessAdapter",
    "ReplayAdapter",
    "get_adapter",
]

__version__ = "0.1.0"
