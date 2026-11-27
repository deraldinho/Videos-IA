"""Testes de regressão CR-02: transicoes artificiais no SQLite.

Defeito: ``GraphOrchestrator._graph_state_to_db`` persiste o status final do
grafo no banco reconstruindo um caminho de transicoes validas (BFS sobre
``ALLOWED_TRANSITIONS``). Quando o grafo termina em um estado distante do
estado atual do video (ex.: ``QUEUED`` ou ``PUBLISHED`` vindo de
``IDEA_CREATED``), ele aplica transicoes estado a estado que o video nunca
realmente atravessou no grafo, gravando historico de estados artificiais
(``SCRIPT_READY``, ``RENDERING``, ... ``QUEUED``) que nao foram executados.

Regra: o banco so deve refletir estados que o video realmente atingiu. Nao deve
criar transicoes artificiais que pulam etapas intermediarias apenas porque o
grafo reportou um estado final distante.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.database import Database, ALLOWED_TRANSITIONS, VIDEO_STATES
from src.errors import PipelineError
from src.graph_orchestrator import GraphOrchestrator
from src.settings import load_settings

# O _validate_required_env_vars le o config.yaml DA RAIZ do projeto (que tem
# live_enabled: true), entao exige credenciais Cloudinary no ambiente. Usamos
# valores ficticios (nao reais) apenas para passar na validacao de presenca.
_FAKE_CLOUDINARY = {
    "CLOUDINARY_CLOUD_NAME": "test-cloud",
    "CLOUDINARY_API_KEY": "test-key",
    "CLOUDINARY_API_SECRET": "test-secret",
}


def _tmp_settings(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mode: REVIEW\nniche: teste\nscript_kind: factual\n"
        "video:\n  min_seconds: 30\n  max_seconds: 60\n  target_seconds: 45\n  ffmpeg: ffmpeg\n  ffprobe: ffprobe\n  node: node\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    # Define variaveis ficticias para passar na validacao de credenciais
    # (o _validate_required_env_vars so checa presenca, nao valida valores reais).
    saved = {k: os.environ.get(k) for k in _FAKE_CLOUDINARY}
    for k, v in _FAKE_CLOUDINARY.items():
        os.environ[k] = v
    try:
        return load_settings(cfg, env)
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_video(db: Database, video_id: str, execution_id: str, status: str = "IDEA_CREATED"):
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
    # Forca o status inicial desejado direto no SQL (bypassa validacao de
    # transicao) apenas para simular estados arbitrarios do banco em teste.
    if status != "IDEA_CREATED":
        with db.transaction() as conn:
            conn.execute(
                "UPDATE videos SET status = ?, updated_at = ? WHERE video_id = ?",
                (status, _utc_iso(), video_id),
            )


def test_graph_state_to_db_nao_cria_transicoes_artificiais_ate_queued(tmp_path):
    """Video em IDEA_CREATED: _graph_state_to_db nao deve gravar historico
    artificial de estados intermediarios (SCRIPT_READY, RENDERING, ... QUEUED)
    apenas porque o grafo terminou em QUEUED."""
    settings = _tmp_settings(tmp_path)
    db = Database(tmp_path / "cr02.sqlite3")
    orch = GraphOrchestrator(settings=settings, db_path=tmp_path / "cr02.sqlite3")
    try:
        _make_video(db, "v1", "exec-1", status="IDEA_CREATED")

        # Simula o estado final do grafo: terminou em QUEUED (distante do atual).
        estado_grafo = {
            "execution_id": "exec-1",
            "video_id": "v1",
            "status": "QUEUED",
            "current_stage": "QUEUED",
            "note": "grafo terminou em QUEUED",
        }
        orch._graph_state_to_db("v1", estado_grafo)

        row = db.get_video("v1")
        # O video deve permanecer no estado real (IDEA_CREATED) porque nao
        # atravessou os estados intermediarios no grafo.
        assert row["status"] == "IDEA_CREATED", (
            f"esperava IDEA_CREATED (sem transicoes artificiais), obteve {row['status']}"
        )

        # O historico nao deve conter estados artificiais intermediarios.
        with db.transaction() as conn:
            historico = conn.execute(
                "SELECT to_status FROM video_history WHERE video_id = ? ORDER BY id",
                ("v1",),
            ).fetchall()
        to_statuses = [h["to_status"] for h in historico]
        artificiais = [
            s for s in to_statuses
            if s in {"SCRIPT_READY", "RENDERING", "RENDERED", "VALIDATED",
                     "AWAITING_APPROVAL", "APPROVED", "UPLOADING", "UPLOADED"}
        ]
        assert not artificiais, (
            f"historico contem transicoes artificiais: {artificiais}"
        )
    finally:
        orch.close()
        db.close()


def test_graph_state_to_db_nao_cria_transicoes_artificiais_ate_published(tmp_path):
    """Video em APPROVED: _graph_state_to_db nao deve gravar historico
    artificial (UPLOADING, UPLOADED, QUEUED, PUBLISHING, PUBLISHED) apenas
    porque o grafo terminou em PUBLISHED."""
    settings = _tmp_settings(tmp_path)
    db = Database(tmp_path / "cr02b.sqlite3")
    orch = GraphOrchestrator(settings=settings, db_path=tmp_path / "cr02b.sqlite3")
    try:
        _make_video(db, "v2", "exec-2", status="APPROVED")

        estado_grafo = {
            "execution_id": "exec-2",
            "video_id": "v2",
            "status": "PUBLISHED",
            "current_stage": "PUBLISHED",
            "note": "grafo terminou em PUBLISHED",
        }
        orch._graph_state_to_db("v2", estado_grafo)

        row = db.get_video("v2")
        assert row["status"] == "APPROVED", (
            f"esperava APPROVED (sem transicoes artificiais), obteve {row['status']}"
        )

        with db.transaction() as conn:
            historico = conn.execute(
                "SELECT to_status FROM video_history WHERE video_id = ? ORDER BY id",
                ("v2",),
            ).fetchall()
        to_statuses = [h["to_status"] for h in historico]
        artificiais = [s for s in to_statuses
                       if s in {"UPLOADING", "UPLOADED", "QUEUED", "PUBLISHING", "PUBLISHED"}]
        assert not artificiais, (
            f"historico contem transicoes artificiais: {artificiais}"
        )
    finally:
        orch.close()
        db.close()


def test_graph_state_to_db_persiste_status_valido_adjacente(tmp_path):
    """Quando o status do grafo e um estado do banco adjacente/valido a partir
    do estado atual, a transicao deve ser aplicada normalmente (sem artificio)."""
    settings = _tmp_settings(tmp_path)
    db = Database(tmp_path / "cr02c.sqlite3")
    orch = GraphOrchestrator(settings=settings, db_path=tmp_path / "cr02c.sqlite3")
    try:
        _make_video(db, "v3", "exec-3", status="IDEA_CREATED")

        estado_grafo = {
            "execution_id": "exec-3",
            "video_id": "v3",
            "status": "SCRIPT_READY",
            "current_stage": "SCRIPT_READY",
            "note": "roteiro pronto",
        }
        orch._graph_state_to_db("v3", estado_grafo)

        row = db.get_video("v3")
        assert row["status"] == "SCRIPT_READY"
    finally:
        orch.close()
        db.close()


__all__ = []
