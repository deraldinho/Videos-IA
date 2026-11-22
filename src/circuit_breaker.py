"""Circuit breaker para serviços externos (Cloudinary, Buffer, TTS, FFmpeg).

Evita chamadas repetidas a serviços que estão falhando, abrindo o circuito
por um tempo configurável. Estados: CLOSED (normal), OPEN (bloqueado),
HALF_OPEN (testando recuperação).
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Callable


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker com fail_count, recovery_timeout e half_open_max_calls."""

    def __init__(
        self,
        *,
        name: str,
        fail_threshold: int = 5,
        recovery_timeout_seconds: float = 30.0,
        half_open_max_calls: int = 3,
    ) -> None:
        self.name = name
        self.fail_threshold = fail_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._fail_count = 0
        self._last_fail_time: float | None = None
        self._half_open_calls = 0

    @property
    def state(self) -> CircuitState:
        self._maybe_transition()
        return self._state

    def _maybe_transition(self) -> None:
        if self._state == CircuitState.OPEN:
            if self._last_fail_time is not None and (
                time.time() - self._last_fail_time >= self.recovery_timeout_seconds
            ):
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0

    def _record_success(self) -> None:
        self._fail_count = 0
        self._state = CircuitState.CLOSED
        self._half_open_calls = 0

    def _record_failure(self) -> None:
        self._fail_count += 1
        self._last_fail_time = time.time()
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
        elif self._fail_count >= self.fail_threshold:
            self._state = CircuitState.OPEN

    def call(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        """Executa ``func`` protegida pelo circuit breaker."""
        if self.state == CircuitState.OPEN:
            raise CircuitBreakerOpenError(
                f"Circuit breaker '{self.name}' está OPEN (serviço indisponível)"
            )

        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            self._record_failure()
            raise

        self._record_success()
        return result

    # Exposto para inspeção/monitoramento.
    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "fail_count": self._fail_count,
            "last_fail_time": self._last_fail_time,
        }


class CircuitBreakerOpenError(Exception):
    """Lançado quando o circuit breaker está OPEN."""


__all__ = ["CircuitBreaker", "CircuitState", "CircuitBreakerOpenError"]
