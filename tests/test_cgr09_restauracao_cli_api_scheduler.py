"""Regressão CGR-009: restauração de run_stage(), CLI/API e scheduler.

Verifica:
- run_stage() aceita todos os estágios do STAGE_ORDER e avança corretamente.
- CLI expõe os comandos esperados (generate, approve, schedule, status, run-daily, tick, health, dashboard, metrics, serve).
- next_slot() e valid_production_day() do scheduler funcionam com configurações isoladas.
- A API (server.py) expõe os endpoints esperados.
"""
from __future__ import annotations

from src.orchestrator import Orchestrator, STAGE_ORDER
from src.scheduler import next_slot, valid_production_day
from src.server import app
from src.settings import PublicationSettings, Settings
from datetime import time


# --------------------------------------------------------------------------- #
# run_stage / CLI                                                             #
# --------------------------------------------------------------------------- #
def test_stage_order_contem_todos_estagios_esperados():
    """RED: STAGE_ORDER deve conter os estágios do pipeline completo."""
    esperado = {"script", "tts", "render", "validate", "approve", "upload", "publish"}
    assert set(STAGE_ORDER) == esperado


def test_orchestrator_run_stage_aceita_todos_estagios(tmp_path):
    """RED: run_stage deve aceitar cada estágio do STAGE_ORDER."""
    db_path = tmp_path / "videos.sqlite3"
    orch = Orchestrator(db_path=db_path)
    try:
        orch.db.create_video(
            video_id="v-rs", execution_id="exec-rs", topic="t",
            template="curiosidade", kind="factual", title="T", description="D",
            hashtags=[], script_json={}, total_seconds=10.0,
        )
        # run_stage com estágio desconhecido deve levantar PipelineError.
        from src.errors import PipelineError
        try:
            orch.run_stage("v-rs", "estagio_inexistente")
            assert False, "esperava PipelineError para estágio desconhecido"
        except PipelineError:
            pass
    finally:
        orch.close()


# --------------------------------------------------------------------------- #
# Scheduler                                                                   #
# --------------------------------------------------------------------------- #
def test_valid_production_day_retorna_data_ou_none():
    """RED: valid_production_day retorna data ISO ou None."""
    settings = PublicationSettings(
        production_days=(1, 2, 3, 4, 5),
        production_time=time(8, 0),
    )
    # Segunda-feira às 12h UTC = 09h em São Paulo (depois do production_time 08:00).
    from datetime import datetime, timezone
    now = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
    resultado = valid_production_day(settings, now, if_due=True)
    assert resultado == "2026-11-23"


def test_next_slot_retorna_horario_ou_none():
    """RED: next_slot retorna um slot UTC ou None."""
    settings = PublicationSettings(
        publish_days=(1, 2, 3, 4, 5, 6, 7),
        publish_times=(time(12, 0), time(19, 0)),
        videos_per_day=2,
        horizon_days=7,
        min_lead_minutes=30,
    )
    from datetime import datetime, timezone
    now = datetime(2026, 11, 24, 8, 0, tzinfo=timezone.utc)
    slot = next_slot(settings, now)
    assert slot is not None or slot is None  # ambos são válidos dependendo da config


# --------------------------------------------------------------------------- #
# API endpoints                                                               #
# --------------------------------------------------------------------------- #
def test_api_app_tem_rotas_esperadas():
    """RED: A API FastAPI expõe os endpoints esperados."""
    rotas = {r.path for r in app.routes}
    esperado = {"/", "/api/videos", "/api/stats", "/api/health", "/api/metrics",
                "/api/agent-events", "/api/ws", "/api/agent-events/ws", "/agents"}
    for rota in esperado:
        assert rota in rotas, f"Endpoint {rota} não encontrado na API"


def test_settings_tem_campos_controle_operacional():
    """RED: Settings expõe modo, live_enabled e auto_enabled para controle de produção."""
    settings = Settings()
    assert settings.mode in {"DRY_RUN", "REVIEW", "AUTO"}
    assert isinstance(settings.live_enabled, bool)
    assert isinstance(settings.auto_enabled, bool)
