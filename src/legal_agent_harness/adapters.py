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
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_OUTPUT_BYTES = 1_000_000

_READ_CHUNK_BYTES = 65536
_POLL_SECONDS = 0.05
_JOIN_SECONDS = 1.0

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
        payload = json.dumps(case, ensure_ascii=False).encode("utf-8")
        returncode, stdout, stderr = self._run(payload)
        if returncode != 0:
            err = stderr.decode("utf-8", "replace")[:500]
            raise AdapterError(f"процесс завершился с кодом {returncode}: {err}")

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

    def _run(self, payload: bytes) -> Tuple[int, bytes, bytes]:
        """Запускает процесс и возвращает (код, stdout, stderr).

        stdout читается порциями: при превышении max_output_bytes процесс
        завершается сразу, а не после накопления всего вывода в памяти.
        stderr хранится не больше max_output_bytes, остаток отбрасывается.
        """
        try:
            proc = subprocess.Popen(
                self._command if not self._use_shell else " ".join(self._command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=self._use_shell,
                cwd=self._cwd,
            )
        except FileNotFoundError as exc:
            raise AdapterError(
                f"исполняемый файл не найден: {self._command[0]!r}"
            ) from exc
        except OSError as exc:
            raise AdapterError(f"не удалось запустить процесс: {exc}") from exc

        limit = self._max_output_bytes
        stdout = bytearray()
        stderr = bytearray()
        overflow = threading.Event()

        def feed() -> None:
            try:
                proc.stdin.write(payload)
            except OSError:
                pass  # процесс не читает stdin или уже завершился
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass

        def drain(stream: Any, buf: bytearray, strict: bool) -> None:
            while True:
                chunk = stream.read1(_READ_CHUNK_BYTES)
                if not chunk:
                    return
                if len(buf) + len(chunk) > limit:
                    buf.extend(chunk[: limit - len(buf)])
                    if strict:
                        overflow.set()
                        return
                else:
                    buf.extend(chunk)

        threads = [
            threading.Thread(target=feed, daemon=True),
            threading.Thread(target=drain, args=(proc.stdout, stdout, True), daemon=True),
            threading.Thread(target=drain, args=(proc.stderr, stderr, False), daemon=True),
        ]
        for thread in threads:
            thread.start()

        deadline = time.monotonic() + self._timeout
        timed_out = False
        while proc.poll() is None and not overflow.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                proc.wait(timeout=min(remaining, _POLL_SECONDS))
            except subprocess.TimeoutExpired:
                pass
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        # Дочерние процессы агента могут удерживать каналы открытыми;
        # потоки-демоны не блокируют harness дольше короткого ожидания.
        join_deadline = time.monotonic() + _JOIN_SECONDS
        for thread in threads:
            thread.join(timeout=max(0.0, join_deadline - time.monotonic()))

        if overflow.is_set():
            raise AdapterError(f"вывод превысил лимит {limit} байт")
        if timed_out:
            raise AdapterError(f"таймаут локального процесса ({self._timeout} c)")
        if threads[1].is_alive():
            raise AdapterError("stdout локального процесса не был закрыт")
        return proc.returncode, bytes(stdout), bytes(stderr)


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
