"""Testes de integração da API para aprovação/rejeição humana (endpoints approve/reject).

Cobre:
- POST /api/videos/{id}/approve  -> 200 e transicao AWAITING_APPROVAL -> UPLOADING
- POST /api/videos/{id}/reject    -> 200 e transicao AWAITING_APPROVAL -> ACTION_REQUIRED
- Video nao encontrado            -> 404
- Video nao esta em AWAITING_APPROVAL -> 409
- Formato da resposta (video_id, from_status, to_status, changed, note)
- Auth por API key quando UI_API_KEY esta configurada (401 sem chave)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import os
import pytest
from fastapi.testclient import TestClient

from src.database import Database
from src.graph_orchestrator import GraphOrchestrator
from src.server import app, get_orchestrator


def _cria_video_aguardando_aprovacao(db_path: Path) -> tuple[str, str]:
    """Cria um vídeo no banco ate AWAITING_APPROVAL e retorna (video_id, execution_id)."""
    video_id = "vid-api-aprovacao"
    execution_id = "exec-api-aprovacao"
    db = Database(str(db_path))
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
    sm = __import__("src.state_machine", fromlist=["StateMachine"]).StateMachine(db)
    for to in ("SCRIPT_READY", "RENDERING", "RENDERED", "VALIDATED", "AWAITING_APPROVAL"):
        sm.advance(video_id, to)
    db.close()
    return video_id, execution_id


def _prime_checkpoint_na_aprovacao(
    orch: GraphOrchestrator, execution_id: str, video_id: str
) -> None:
    """Executa o grafo ate a interrupcao de aprovacao humana (checkpoint persistente)."""
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


def _instala_orchestrator_no_server(orch: GraphOrchestrator) -> None:
    """Substitui o orchestrator singleton do server pelo informado."""
    import src.server as server_mod

    server_mod._orchestrator = orch


def _reseta_orchestrator_do_server() -> None:
    """Limpa o orchestrator singleton do server (isolamento entre testes)."""
    import src.server as server_mod

    server_mod._orchestrator = None


@pytest.fixture
def cliente_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """TestClient com orchestrator isolado em banco temporario."""
    db_path = tmp_path / "api.sqlite3"
    video_id, execution_id = _cria_video_aguardando_aprovacao(db_path)

    orch = GraphOrchestrator(db_path=str(db_path))
    _prime_checkpoint_na_aprovacao(orch, execution_id, video_id)
    _instala_orchestrator_no_server(orch)

    # Desabilita auth por API key por default (UI_API_KEY nao definida).
    monkeypatch.delenv("UI_API_KEY", raising=False)

    client = TestClient(app)
    try:
        yield client, video_id
    finally:
        _reseta_orchestrator_do_server()
        orch.close()


# --------------------------------------------------------------------------- #
# Aprovacao                                                                   #
# --------------------------------------------------------------------------- #
def test_api_aprovacao_retorna_200_e_transiciona(cliente_api: Any) -> None:
    """POST approve em video AWAITING_APPROVAL retorna 200 e corpo esperado."""
    client, video_id = cliente_api
    resp = client.post(f"/api/videos/{video_id}/approve", json={"actor": "operador"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["video_id"] == video_id
    assert body["from_status"] == "AWAITING_APPROVAL"
    assert body["to_status"] == "UPLOADING"
    assert body["changed"] is True
    note_lower = body["note"].lower()
    assert "aprov" in note_lower or "approved" in note_lower


def test_api_aprovacao_persiste_no_banco(cliente_api: Any) -> None:
    """Aprovacao via API persiste a mudanca de estado no SQLite."""
    client, video_id = cliente_api
    client.post(f"/api/videos/{video_id}/approve", json={"actor": "operador"})

    # Usa o orchestrator ja instalado no server para ler o banco.
    import src.server as server_mod

    orch = server_mod._orchestrator
    assert orch is not None
    row = orch.db.get_video(video_id)
    assert row["status"] == "UPLOADING"


# --------------------------------------------------------------------------- #
# Rejecao                                                                     #
# --------------------------------------------------------------------------- #
def test_api_rejecao_retorna_200_e_transiciona(cliente_api: Any) -> None:
    """POST reject em video AWAITING_APPROVAL retorna 200 e corpo esperado."""
    client, video_id = cliente_api
    resp = client.post(
        f"/api/videos/{video_id}/reject",
        json={"actor": "operador", "motivo": "roteiro inadequado"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["video_id"] == video_id
    assert body["from_status"] == "AWAITING_APPROVAL"
    assert body["to_status"] == "ACTION_REQUIRED"
    assert body["changed"] is True


def test_api_rejecao_persiste_no_banco(cliente_api: Any) -> None:
    """Rejecao via API persiste a mudanca de estado no SQLite."""
    client, video_id = cliente_api
    client.post(
        f"/api/videos/{video_id}/reject",
        json={"actor": "operador", "motivo": "roteiro inadequado"},
    )
    import src.server as server_mod

    orch = server_mod._orchestrator
    assert orch is not None
    row = orch.db.get_video(video_id)
    assert row["status"] == "ACTION_REQUIRED"


# --------------------------------------------------------------------------- #
# Erros                                                                       #
# --------------------------------------------------------------------------- #
def test_api_aprovacao_video_inexistente_retorna_404(cliente_api: Any) -> None:
    client, _ = cliente_api
    resp = client.post("/api/videos/nao-existe/approve", json={"actor": "operador"})
    assert resp.status_code == 404


def test_api_rejecao_video_inexistente_retorna_404(cliente_api: Any) -> None:
    client, _ = cliente_api
    resp = client.post("/api/videos/nao-existe/reject", json={"actor": "operador"})
    assert resp.status_code == 404


def test_api_aprovacao_fora_de_aguardando_retorna_409(cliente_api: Any) -> None:
    client, video_id = cliente_api
    # Primeiro rejeita (sai de AWAITING_APPROVAL).
    client.post(
        f"/api/videos/{video_id}/reject",
        json={"actor": "operador", "motivo": "x"},
    )
    # Agora tentar aprovar deve falhar com 409.
    resp = client.post(f"/api/videos/{video_id}/approve", json={"actor": "operador"})
    assert resp.status_code == 409


def test_api_rejecao_fora_de_aguardando_retorna_409(cliente_api: Any) -> None:
    client, video_id = cliente_api
    # Primeiro aprova (sai de AWAITING_APPROVAL).
    client.post(f"/api/videos/{video_id}/approve", json={"actor": "operador"})
    # Agora tentar rejeitar deve falhar com 409.
    resp = client.post(
        f"/api/videos/{video_id}/reject",
        json={"actor": "operador", "motivo": "x"},
    )
    assert resp.status_code == 409


# --------------------------------------------------------------------------- #
# Autenticacao                                                                #
# --------------------------------------------------------------------------- #
def test_api_aprovacao_sem_api_key_quando_configurada_retorna_401(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sem header X-API-Key quando UI_API_KEY definida => 401."""
    db_path = tmp_path / "auth.sqlite3"
    video_id, execution_id = _cria_video_aguardando_aprovacao(db_path)
    orch = GraphOrchestrator(db_path=str(db_path))
    _prime_checkpoint_na_aprovacao(orch, execution_id, video_id)
    _instala_orchestrator_no_server(orch)

    import src.server as server_mod_auth

    ui_api_key_original = server_mod_auth.UI_API_KEY
    server_mod_auth.UI_API_KEY = "chave-secreta"
    client = TestClient(app)
    try:
        resp = client.post(f"/api/videos/{video_id}/approve", json={"actor": "operador"})
        assert resp.status_code == 401
    finally:
        _reseta_orchestrator_do_server()
        orch.close()
        server_mod_auth.UI_API_KEY = ui_api_key_original
        monkeypatch.delenv("UI_API_KEY", raising=False)


# --------------------------------------------------------------------------- #
# Demais endpoints da API (ampliacao da cobertura de server.py)               #
# --------------------------------------------------------------------------- #
def test_api_root_retorna_info(cliente_api: Any) -> None:
    client, _ = cliente_api
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "TikTok AI Autopilot — UI ao Vivo"
    assert "/api/videos" in body["endpoints"]["videos"]


def test_api_list_videos_sem_filtro(cliente_api: Any) -> None:
    client, video_id = cliente_api
    resp = client.get("/api/videos")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] >= 1
    assert any(v["video_id"] == video_id for v in body["videos"])


def test_api_list_videos_com_filtro_status(cliente_api: Any) -> None:
    client, video_id = cliente_api
    resp = client.get(f"/api/videos?status=AWAITING_APPROVAL")
    assert resp.status_code == 200
    body = resp.json()
    assert all(v["status"] == "AWAITING_APPROVAL" for v in body["videos"])


def test_api_detail_video_encontrado(cliente_api: Any) -> None:
    client, video_id = cliente_api
    resp = client.get(f"/api/videos/{video_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["video_id"] == video_id
    assert body["status"] == "AWAITING_APPROVAL"


def test_api_detail_video_nao_encontrado_retorna_404(cliente_api: Any) -> None:
    client, _ = cliente_api
    resp = client.get("/api/videos/nao-existe")
    assert resp.status_code == 404


def test_api_stats_retorna_agregados(cliente_api: Any) -> None:
    client, _ = cliente_api
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body and isinstance(body["total"], int)
    assert "by_status" in body and isinstance(body["by_status"], dict)


def test_api_health_retorna_report(cliente_api: Any) -> None:
    client, _ = cliente_api
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "healthy" in body and "checks" in body


def test_api_metrics_retorna_snapshot(cliente_api: Any) -> None:
    client, _ = cliente_api
    resp = client.get("/api/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert "uptime_seconds" in body
    assert "counters" in body
    assert "gauges" in body


def test_api_generate_sem_topic_retorna_422(cliente_api: Any) -> None:
    client, _ = cliente_api
    # topic e obrigatorio (Field(...)) => 422 de validacao do Pydantic.
    resp = client.post("/api/videos", json={"kind": "factual", "template": "curiosidade"})
    assert resp.status_code == 422


def test_api_retry_video_inexistente_retorna_404(cliente_api: Any) -> None:
    client, _ = cliente_api
    resp = client.post("/api/videos/nao-existe/retry")
    assert resp.status_code == 404


def test_api_retry_video_nao_failed_retorna_409(cliente_api: Any) -> None:
    client, video_id = cliente_api  # esta em AWAITING_APPROVAL
    resp = client.post(f"/api/videos/{video_id}/retry")
    assert resp.status_code == 409


def test_api_advance_estagio_desconhecido_retorna_422(cliente_api: Any) -> None:
    client, video_id = cliente_api
    # AdvanceRequest exige 'stage' no body => 422 de validacao do Pydantic.
    resp = client.post(f"/api/advance/{video_id}/script", json={})
    assert resp.status_code == 422


def test_api_advance_video_inexistente_retorna_404(cliente_api: Any) -> None:
    client, _ = cliente_api
    resp = client.post("/api/advance/nao-existe/script", json={"stage": "script"})
    assert resp.status_code == 404


def test_api_advance_fora_de_estado_valido_retorna_200(cliente_api: Any) -> None:
    """AWAITING_APPROVAL -> script e uma transicao adjacente valida => 200."""
    client, video_id = cliente_api
    resp = client.post(f"/api/advance/{video_id}/script", json={"stage": "script"})
    assert resp.status_code == 200


def test_api_aprovacao_com_api_key_valida_retorna_200(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Com header X-API-Key correto quando UI_API_KEY definida => 200."""
    db_path = tmp_path / "auth_ok.sqlite3"
    video_id, execution_id = _cria_video_aguardando_aprovacao(db_path)
    orch = GraphOrchestrator(db_path=str(db_path))
    _prime_checkpoint_na_aprovacao(orch, execution_id, video_id)
    _instala_orchestrator_no_server(orch)

    import src.server as server_mod_auth

    ui_api_key_original = server_mod_auth.UI_API_KEY
    server_mod_auth.UI_API_KEY = "chave-secreta"
    client = TestClient(app)
    try:
        resp = client.post(
            f"/api/videos/{video_id}/approve",
            json={"actor": "operador"},
            headers={"X-API-Key": "chave-secreta"},
        )
        assert resp.status_code == 200, resp.text
    finally:
        _reseta_orchestrator_do_server()
        orch.close()
        server_mod_auth.UI_API_KEY = ui_api_key_original
        monkeypatch.delenv("UI_API_KEY", raising=False)

