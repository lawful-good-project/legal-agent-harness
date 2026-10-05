"""Адаптеры калибровочного harness.

Все адаптеры локальные. Сеть не используется.
- ReplayAdapter: возвращает заранее заданный JSON-ответ.
- LocalProcessAdapter: вызывает только локальный исполняемый файл/скрипт
  с жёстким таймаутом и ограничением размера вывода. Никакой сети.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Sequence

DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000

# Переменные окружения, запрещающие неявную сетевую активность не форсируем,
# но явно предупреждаем: адаптер запускает только указанную пользователем
# локальную команду и не делает собственных сетевых запросов.


class AdapterError(Exception):
    """Ошибка адаптера (таймаут, переполнение вывода, ненулевой код)."""


class Adapter:
    """Базовый интерфейс адаптера."""

    name = "base"

    def respond(self, case: Dict[str, Any]) -> Dict[str, Any]:  # pragma: no cover
        raise NotImplementedError


class ReplayAdapter(Adapter):
    """Возвращает заранее заготовленный JSON-ответ по case_id.

    Ответ не копируется: некорректный ответ должен проходить валидацию.
    """

    name = "replay"

    def __init__(self, responses: Dict[str, Dict[str, Any]]) -> None:
        self._responses = dict(responses)

    def respond(self, case: Dict[str, Any]) -> Dict[str, Any]:
        case_id = case.get("case_id")
        if case_id not in self._responses:
            raise AdapterError(f"нет заготовленного ответа для case_id={case_id!r}")
        payload = self._responses[case_id]
        response = payload.get("response", payload)
        if not isinstance(response, dict):
            raise AdapterError(
                f"ответ для case_id={case_id!r} должен быть JSON-объектом"
            )
        return json.loads(json.dumps(response))


class LocalProcessAdapter(Adapter):
    """Запускает локальный процесс и читает JSON-ответ из stdout.

    Только локальный процесс: переданная пользователем команда либо
    интерпретируется через shlex без shell, либо (если use_shell=True)
    через shell. Собственных сетевых запросов адаптер не делает.
    Ограничения: timeout (секунды) и max_output_bytes.
    """

    name = "local-process"

    def __init__(
        self,
        command: Sequence[str],
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        use_shell: bool = False,
        cwd: Optional[str] = None,
    ) -> None:
        if isinstance(command, str):
            command = (
                command if use_shell else shlex.split(command, posix=os.name != "nt")
            )
        if not command:
            raise AdapterError("команда не задана")
        if timeout <= 0:
            raise AdapterError("timeout должен быть > 0")
        if max_output_bytes <= 0:
            raise AdapterError("max_output_bytes должен быть > 0")
        self._command: List[str] = list(command)
        self._timeout = float(timeout)
        self._max_output_bytes = int(max_output_bytes)
        self._use_shell = use_shell
        self._cwd = cwd

    @property
    def command(self) -> List[str]:
        return list(self._command)

    def respond(self, case: Dict[str, Any]) -> Dict[str, Any]:
        payload = json.dumps(case, ensure_ascii=False)
        try:
            proc = subprocess.run(
                self._command if not self._use_shell else " ".join(self._command),
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self._timeout,
                shell=self._use_shell,
                cwd=self._cwd,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdapterError(
                f"таймаут локального процесса ({self._timeout} c)"
            ) from exc
        except FileNotFoundError as exc:
            raise AdapterError(
                f"исполняемый файл не найден: {self._command[0]!r}"
            ) from exc

        stdout = proc.stdout or b""
        if len(stdout) > self._max_output_bytes:
            raise AdapterError(f"вывод превысил лимит {self._max_output_bytes} байт")
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace")[:500]
            raise AdapterError(f"процесс завершился с кодом {proc.returncode}: {err}")

        text = stdout.decode("utf-8", "replace").strip()
        if not text:
            raise AdapterError("пустой stdout локального процесса")
        try:
            response = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AdapterError(f"некорректный JSON в stdout: {exc}") from exc
        if not isinstance(response, dict):
            raise AdapterError("stdout должен содержать JSON-объект")
        return response


def get_adapter(kind: str, **kwargs: Any) -> Adapter:
    """Фабрика адаптеров по имени: replay | local-process."""
    kind = (kind or "").strip().lower()
    if kind == "replay":
        responses = kwargs.get("responses")
        if responses is None:
            raise AdapterError("для replay требуется responses")
        return ReplayAdapter(responses)
    if kind in ("local-process", "local", "process"):
        command = kwargs.get("command")
        if not command:
            raise AdapterError("для local-process требуется command")
        return LocalProcessAdapter(
            command,
            timeout=kwargs.get("timeout", DEFAULT_TIMEOUT_SECONDS),
            max_output_bytes=kwargs.get("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES),
            use_shell=kwargs.get("use_shell", False),
            cwd=kwargs.get("cwd"),
        )
    raise AdapterError(f"неизвестный тип адаптера: {kind!r}")


def has_python() -> bool:
    """Проверка наличия локального интерпретатора Python."""
    return bool(shutil.which(sys.executable or "python"))
