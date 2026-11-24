"""Testes de regressão CR-06: contratos CLI e API restaurados.

Defeito: a migração do orquestrador original (Orchestrator/state_machine/STAGE_ORDER)
para o GraphOrchestrator (LangGraph) pode ter removido ou alterado contratos que a
CLI (main.py) e a API (server.py) dependem, quebrando pontos de integração.

Contratos que devem permanecer funcionais:
- `Orchestrator` ainda resolvível (alias para GraphOrchestrator) para entry points antigos.
- `STAGE_ORDER` exportado de `src.orchestrator` (usado pela API /api/advance/{stage}).
- `GraphOrchestrator.approve` / `reject` / `resume_after_approval` (CLI approve/reject).
- `GraphOrchestrator.run_stage(video_id, stage)` (API advance).
- CLI registra comando `tick`.
- API expõe endpoints /api/videos, /api/stats, /api/health, /api/metrics.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# CR-06: contratos CLI/API                                                    #
# --------------------------------------------------------------------------- #
def test_orchestrator_alias_resolve():
    """Orchestrator ainda resolvivel (compatibilidade com entry points antigos)."""
    from main import Orchestrator, GraphOrchestrator
    assert Orchestrator is GraphOrchestrator


def test_stage_order_exportado_pelo_orchestrador():
    """STAGE_ORDER exportado de src.orchestrator (usado pela API)."""
    from src.orchestrator import STAGE_ORDER
    assert STAGE_ORDER == [
        "script", "tts", "render", "validate", "approve", "upload", "publish",
    ]


def test_tick_command_registrado():
    """Comando tick registrado no CLI."""
    import main
    assert "tick" in main.COMMANDS
    assert main.COMMANDS["tick"] is main.cmd_tick


def test_graph_orchestrator_tem_approve_reject_run_stage():
    """GraphOrchestrator expõe approve, reject, resume_after_approval e run_stage."""
    from src.graph_orchestrator import GraphOrchestrator
    for nome in ("approve", "reject", "resume_after_approval", "run_stage",
                 "run_pipeline", "generate", "status", "list_by_status"):
        assert hasattr(GraphOrchestrator, nome), f"metodo ausente: {nome}"


def test_api_app_tem_endpoints_esperados():
    """API FastAPI expõe os endpoints esperados."""
    from src.server import app
    paths = {route.path for route in app.routes}
    esperado = {
        "/", "/api/videos", "/api/videos/{video_id}", "/api/stats",
        "/api/health", "/api/metrics", "/api/agent-events",
        "/api/videos/{video_id}/approve", "/api/videos/{video_id}/reject",
        "/api/videos/{video_id}/retry", "/api/advance/{video_id}/{stage}",
        "/api/ws", "/api/agent-events/ws", "/agents",
    }
    assert esperado.issubset(paths), f"faltando: {esperado - paths}"


__all__ = []
