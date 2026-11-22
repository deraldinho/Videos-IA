"""Retry com exponential backoff para operações que podem falhar temporariamente.

Fornece ``retry_call`` que envolve uma função e a reexecuta até ``max_attempts``
vezes com backoff exponencial entre tentativas. Exceções não são suprimidas
após esgotar as tentativas.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Tuple, Type


def retry_call(
    func: Callable[..., Any],
    *args,
    max_attempts: int = 3,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 30.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: Tuple[Type[BaseException], ...] = (Exception,),
    on_retry: Callable[[int, Exception], None] | None = None,
    **kwargs,
) -> Any:
    """Chama ``func`` com retry e exponential backoff.

    - ``max_attempts``: número total de tentativas (incluindo a primeira).
    - ``base_delay_seconds``: atraso inicial entre tentativas.
    - ``max_delay_seconds``: atraso máximo entre tentativas.
    - ``backoff_factor``: multiplicador do atraso a cada tentativa.
    - ``retryable_exceptions``: tipos de exceção que disparam retry.
    - ``on_retry``: callback chamado antes de cada retry (tentativa, exceção).
    """
    last_exc: BaseException | None = None
    delay = base_delay_seconds

    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            if on_retry is not None:
                try:
                    on_retry(attempt, exc)
                except Exception:  # noqa: BLE001 - callback não deve quebrar o retry
                    pass
            time.sleep(min(delay, max_delay_seconds))
            delay *= backoff_factor

    # Se chegou aqui, todas as tentativas falharam.
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("retry_call falhou sem exceção registrada")


__all__ = ["retry_call"]
