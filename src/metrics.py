"""Métricas operacionais para o TikTok AI Autopilot.

Contadores e gauge simples (em memória) para observabilidade:
- vídeos por status
- execuções por agente
- erros por estágio
- custo total de tokens
- duração de execução

Não depende de Prometheus; expõe um dicionário simples que pode ser
serailizado para JSON ou consumido por um dashboard.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any


class Metrics:
    """Coletor de métricas thread-safe (em memória)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = defaultdict(float)
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._start_time = time.time()

    # ------------------------------------------------------------------ counters
    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    # ------------------------------------------------------------------ gauges
    def set(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = float(value)

    # ------------------------------------------------------------------ histograms
    def observe(self, name: str, value: float) -> None:
        with self._lock:
            self._histograms[name].append(float(value))

    # ------------------------------------------------------------------ snapshot
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            histograms = {
                name: {
                    "count": len(vals),
                    "sum": sum(vals),
                    "avg": (sum(vals) / len(vals)) if vals else 0.0,
                    "max": max(vals) if vals else 0.0,
                    "min": min(vals) if vals else 0.0,
                }
                for name, vals in self._histograms.items()
            }
            return {
                "uptime_seconds": time.time() - self._start_time,
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": histograms,
            }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


# Instância singleton global.
metrics = Metrics()


def increment_video_status(status: str) -> None:
    metrics.inc(f"videos_status_{status}")


def increment_agent_run(agent: str) -> None:
    metrics.inc(f"agent_runs_{agent}")


def increment_error(stage: str) -> None:
    metrics.inc(f"errors_{stage}")


def increment_stage_attempt(stage: str) -> None:
    """Increment the attempt counter for a pipeline stage."""
    metrics.inc(f"stage_attempts_{stage}")


def increment_stage_success(stage: str) -> None:
    """Increment the success counter for a pipeline stage."""
    metrics.inc(f"stage_successes_{stage}")


def increment_tick() -> None:
    metrics.inc("ticks_total")


def add_cost(agent: str, tokens: int, cost_usd: float) -> None:
    metrics.inc(f"cost_tokens_{agent}", tokens)
    metrics.inc(f"cost_usd_total", int(cost_usd * 1_000_000))  # micro-dólares


def observe_duration(operation: str, seconds: float) -> None:
    metrics.observe(f"duration_{operation}", seconds)


__all__ = [
    "Metrics",
    "metrics",
    "increment_video_status",
    "increment_agent_run",
    "increment_error",
    "increment_stage_attempt",
    "increment_stage_success",
    "add_cost",
    "observe_duration",
]
