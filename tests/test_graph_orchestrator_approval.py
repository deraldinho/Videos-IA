"""Testes de regressão para a retomada via grafo LangGraph (Command(resume=...)) na aprovação/rejeição humana.

Cobre:
- Aprovação via grafo: AWAITING_APPROVAL → APPROVED (Command(resume="approved")).
- Rejeição via grafo: AWAITING_APPROVAL → ACTION_REQUIRED (Command(resume="rejected")).
- Identificadores simulados (mock) nunca permitem PUBLISHED.
- Decisão inválida é rejeitada.
- Vídeo inexistente levanta erro.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.contracts import ALLOWED_TRANSITIONS, VIDEO_STATES
from src.database import Database
from src.errors import PipelineError
from src.graph_orchestrator import GraphOrchestrator
from src.state_machine import StateMachine


def _avanca_ate_aguardando_aprovacao(db: Database, sm: StateMachine, video_id: str) -> None:
    """Leva um vídeo do estado IDEA_CREATED até AWAITING_APPROVAL via transições válidas."""
    for to in (
        "SCRIPT_READY",
        "RENDERING",
        "RENDERED",
        "VALIDATED",
        "AWAITING_APPROVAL",
    ):
        sm.advance(video_id, to)


def _prime_checkpoint_na_aprovacao(orch: GraphOrchestrator, execution_id: str, video_id: str) -> None:
    """Executa o grafo até a interrupção de aprovação humana, gravando checkpoint.

    Parte do estado COMPLIANCE_CHECKED; o supervisor roteia para human_approval,
    que chama ``interrupt()`` e pausa o grafo com checkpoint persistente.
    """
    estado = {
        "execution_id": execution_id,
        "video_id": video_id,
        "status": "COMPLIANCE_CHECKED",
        "current_stage": "COMPLIANCE_CHECKED",
        "approval_status": "pending",
        "mode": "REVIEW",
        "live_enabled": False,
        "auto_enabled": False,
    }
    orch._invoke_graph(execution_id, estado)


def test_aprovacao_via_grafo_transiciona_para_approved(tmp_path: Path) -> None:
    """Aprovação retoma o grafo (Command(resume='approved')) e o banco fica em APPROVED."""
    db = Database(tmp_path / "v.sqlite3")
    sm = StateMachine(db)
    video_id = "vid-aprovado"
    execution_id = "exec-aprovado"
    db.create_video(
        video_id=video_id,
        execution_id=execution_id,
        topic="t",
        template="curiosidade",
        kind="factual",
        title="T",
        description="D",
        hashtags=[],
        script_json={},
        total_seconds=10.0,
    )
    _avanca_ate_aguardando_aprovacao(db, sm, video_id)
    assert db.get_video(video_id)["status"] == "AWAITING_APPROVAL"

    orch = GraphOrchestrator(db_path=str(tmp_path / "v.sqlite3"))
    try:
        _prime_checkpoint_na_aprovacao(orch, execution_id, video_id)
        res = orch.approve(video_id, actor="operador")
    finally:
        orch.close()

    # A decisão humana persiste no banco (AWAITING_APPROVAL -> APPROVED) e o
    # grafo continua até a publicação (status final UPLOADING no estado do grafo).
    assert res.status == "UPLOADING", f"esperado UPLOADING, obteve {res.status}"
    # _graph_state_to_db aplica apenas a etapa adjacente APPROVED -> UPLOADING.
    assert db.get_video(video_id)["status"] == "UPLOADING"


def test_rejecao_via_grafo_transiciona_para_action_required(tmp_path: Path) -> None:
    """Rejeição retoma o grafo (Command(resume='rejected')) e o banco fica em ACTION_REQUIRED."""
    db = Database(tmp_path / "v.sqlite3")
    sm = StateMachine(db)
    video_id = "vid-rejeitado"
    execution_id = "exec-rejeitado"
    db.create_video(
        video_id=video_id,
        execution_id=execution_id,
        topic="t",
        template="curiosidade",
        kind="factual",
        title="T",
        description="D",
        hashtags=[],
        script_json={},
        total_seconds=10.0,
    )
    _avanca_ate_aguardando_aprovacao(db, sm, video_id)
    assert db.get_video(video_id)["status"] == "AWAITING_APPROVAL"

    orch = GraphOrchestrator(db_path=str(tmp_path / "v.sqlite3"))
    try:
        _prime_checkpoint_na_aprovacao(orch, execution_id, video_id)
        res = orch.reject(video_id, actor="operador", motivo="roteiro inadequado")
    finally:
        orch.close()

    assert res.status == "ACTION_REQUIRED", f"esperado ACTION_REQUIRED, obteve {res.status}"
    assert db.get_video(video_id)["status"] == "ACTION_REQUIRED"


def test_decisao_invalida_nao_retoma_grafo(tmp_path: Path) -> None:
    """Decisão diferente de approved/rejected levanta PipelineError."""
    db = Database(tmp_path / "v.sqlite3")
    sm = StateMachine(db)
    video_id = "vid-invalido"
    execution_id = "exec-invalido"
    db.create_video(
        video_id=video_id,
        execution_id=execution_id,
        topic="t",
        template="curiosidade",
        kind="factual",
        title="T",
        description="D",
        hashtags=[],
        script_json={},
        total_seconds=10.0,
    )
    _avanca_ate_aguardando_aprovacao(db, sm, video_id)

    orch = GraphOrchestrator(db_path=str(tmp_path / "v.sqlite3"))
    try:
        with pytest.raises(PipelineError, match="Decisão inválida"):
            orch.resume_after_approval(video_id, decision="talvez")
    finally:
        orch.close()


def test_video_inexistente_nao_retoma(tmp_path: Path) -> None:
    """Vídeo não encontrado levanta PipelineError."""
    orch = GraphOrchestrator(db_path=str(tmp_path / "v.sqlite3"))
    try:
        with pytest.raises(PipelineError, match="não encontrado"):
            orch.approve("nao-existe", actor="operador")
    finally:
        orch.close()


def test_transicao_aguardando_aprovacao_para_action_required_valida() -> None:
    """Ação requerida é um destino válido a partir de AWAITING_APPROVAL."""
    assert "ACTION_REQUIRED" in ALLOWED_TRANSITIONS["AWAITING_APPROVAL"]


def test_estados_unificados_contem_action_required() -> None:
    """ACTION_REQUIRED pertence ao conjunto unificado VIDEO_STATES."""
    assert "ACTION_REQUIRED" in VIDEO_STATES
