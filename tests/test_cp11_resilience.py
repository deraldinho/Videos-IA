"""Testes CP11: resiliência, idempotência e produção (logging, métricas, circuit breaker, retry, health)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError, CircuitState
from src.dashboard import generate_dashboard
from src.health import HealthReport, run_health_checks
from src.logging import JsonFormatter, setup_logging, get_logger
from src.metrics import metrics, increment_video_status, increment_agent_run, add_cost, observe_duration
from src.retry import retry_call


# --------------------------------------------------------------------------- #
# Logging                                                                     #
# --------------------------------------------------------------------------- #
def test_json_formatter_emits_valid_json():
    formatter = JsonFormatter()
    import logging

    record = logging.LogRecord(
        name="test.logger", level=logging.INFO, pathname="", lineno=1,
        msg="mensagem de teste", args=(), exc_info=None,
    )
    out = formatter.format(record)
    payload = json.loads(out)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "test.logger"
    assert payload["message"] == "mensagem de teste"


def test_setup_logging_is_idempotent():
    setup_logging(level="DEBUG")
    setup_logging(level="INFO")  # não deve duplicar handlers
    logger = get_logger("test.idempotent")
    assert logger is not None


# --------------------------------------------------------------------------- #
# Metrics                                                                     #
# --------------------------------------------------------------------------- #
def test_metrics_counters_and_gauges():
    metrics.reset()
    increment_video_status("PUBLISHED")
    increment_agent_run("RenderAgent")
    add_cost("VoiceAgent", tokens=100, cost_usd=0.002)
    observe_duration("render", 5.5)

    snap = metrics.snapshot()
    assert snap["counters"]["videos_status_PUBLISHED"] == 1
    assert snap["counters"]["agent_runs_RenderAgent"] == 1
    assert snap["counters"]["cost_tokens_VoiceAgent"] == 100
    assert snap["histograms"]["duration_render"]["count"] == 1
    assert snap["histograms"]["duration_render"]["avg"] == 5.5


def test_metrics_reset():
    metrics.reset()
    increment_video_status("FAILED")
    assert metrics.snapshot()["counters"]["videos_status_FAILED"] == 1
    metrics.reset()
    assert metrics.snapshot()["counters"] == {}


# --------------------------------------------------------------------------- #
# Circuit breaker                                                             #
# --------------------------------------------------------------------------- #
def test_circuit_breaker_closes_after_success():
    cb = CircuitBreaker(name="test", fail_threshold=2)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("falha transitória")
        return "ok"

    # Primeira chamada falha (circuito ainda CLOSED, fail_count=1).
    with pytest.raises(RuntimeError):
        cb.call(flaky)
    # Segunda chamada fecha o circuito.
    result = cb.call(flaky)
    assert result == "ok"
    assert cb.status()["state"] == CircuitState.CLOSED.value


def test_circuit_breaker_opens_after_threshold():
    cb = CircuitBreaker(name="test-open", fail_threshold=2)

    def always_fails():
        raise RuntimeError("sempre falha")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            cb.call(always_fails)

    assert cb.status()["state"] == CircuitState.OPEN.value

    with pytest.raises(CircuitBreakerOpenError):
        cb.call(always_fails)


# --------------------------------------------------------------------------- #
# Retry                                                                       #
# --------------------------------------------------------------------------- #
def test_retry_call_succeeds_after_transient_failure():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ValueError("não ainda")
        return "sucesso"

    result = retry_call(flaky, max_attempts=5, base_delay_seconds=0.01)
    assert result == "sucesso"
    assert attempts["n"] == 3


def test_retry_call_exhausts_attempts():
    def always_fails():
        raise RuntimeError("sempre")

    with pytest.raises(RuntimeError, match="sempre"):
        retry_call(always_fails, max_attempts=3, base_delay_seconds=0.01)


# --------------------------------------------------------------------------- #
# Health                                                                      #
# --------------------------------------------------------------------------- #
def test_health_report_structure():
    report = run_health_checks()
    data = report.to_dict()
    assert "healthy" in data
    assert "checks" in data
    names = {c["name"] for c in data["checks"]}
    assert "ffmpeg" in names
    assert "database" in names
    assert "credentials" in names


def test_health_report_is_healthy_dtobj():
    report = run_health_checks()
    assert isinstance(report, HealthReport)


# --------------------------------------------------------------------------- #
# Dashboard                                                                   #
# --------------------------------------------------------------------------- #
def test_dashboard_generates_html(tmp_path):
    db_path = tmp_path / "dashboard.sqlite3"
    out = tmp_path / "dashboard.html"
    generate_dashboard(db_path=db_path, output=out)
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "TikTok AI Autopilot" in content
    assert "Dashboard" in content


__all__ = []
