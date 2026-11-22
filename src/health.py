"""Health checks do sistema TikTok AI Autopilot.

Verifica a saúde das dependências (FFmpeg, banco de dados, credentials)
e devolve um relatório simples consumível por CLI/monitoramento.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.database import Database, DEFAULT_DB_PATH


@dataclass
class HealthCheck:
    """Resultado de um health check individual."""

    name: str
    healthy: bool
    detail: str = ""
    latency_ms: float | None = None


@dataclass
class HealthReport:
    """Relatório agregado de saúde do sistema."""

    healthy: bool = True
    checks: list[HealthCheck] = field(default_factory=list)

    def add(self, check: HealthCheck) -> None:
        self.checks.append(check)
        if not check.healthy:
            self.healthy = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "checks": [
                {
                    "name": c.name,
                    "healthy": c.healthy,
                    "detail": c.detail,
                    "latency_ms": c.latency_ms,
                }
                for c in self.checks
            ],
        }


def _check_ffmpeg() -> HealthCheck:
    start = _now_ms()
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    latency = _now_ms() - start
    if ffmpeg and ffprobe:
        return HealthCheck("ffmpeg", True, f"ffmpeg={ffmpeg}, ffprobe={ffprobe}", latency)
    return HealthCheck("ffmpeg", False, "ffmpeg ou ffprobe não encontrados no PATH", latency)


def _check_database(db_path: Path = DEFAULT_DB_PATH) -> HealthCheck:
    start = _now_ms()
    try:
        db = Database(db_path)
        with db.transaction() as conn:
            conn.execute("SELECT 1").fetchone()
        latency = _now_ms() - start
        return HealthCheck("database", True, f"SQLite OK ({db_path})", latency)
    except Exception as exc:  # noqa: BLE001
        latency = _now_ms() - start
        return HealthCheck("database", False, f"Falha: {exc}", latency)


def _check_env_credentials() -> HealthCheck:
    start = _now_ms()
    latency = _now_ms() - start
    from src.settings import load_settings

    try:
        settings = load_settings()
        issues = []
        if settings.live_enabled and not settings.cloudinary.api_key:
            issues.append("CLOUDINARY_API_KEY ausente (modo live)")
        if settings.live_enabled and not settings.cloudinary.api_secret:
            issues.append("CLOUDINARY_API_SECRET ausente (modo live)")
        if settings.mode == "AUTO" and not settings.auto_enabled:
            issues.append("mode=AUTO sem auto_enabled")
        if issues:
            return HealthCheck("credentials", False, "; ".join(issues), latency)
        return HealthCheck("credentials", True, f"modo={settings.mode}, live={settings.live_enabled}", latency)
    except Exception as exc:  # noqa: BLE001
        return HealthCheck("credentials", False, f"Falha ao carregar config: {exc}", latency)


def _now_ms() -> float:
    import time

    return time.time() * 1000.0


def run_health_checks(db_path: Path | str | None = None) -> HealthReport:
    """Executa todos os health checks e devolve o relatório."""
    report = HealthReport()

    report.add(_check_ffmpeg())
    report.add(_check_database(Path(db_path) if db_path else DEFAULT_DB_PATH))
    report.add(_check_env_credentials())

    return report


__all__ = ["HealthCheck", "HealthReport", "run_health_checks"]
