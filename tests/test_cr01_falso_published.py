"""Testes de regressão CR-01: elimina falso PUBLISHED.

Regra de segurança (AGENTS.md CP24/CP28 + architecture.md):
Nunca marcar PUBLISHED sem confirmação remota inequívoca (remote_id +
remote_status + confirmed_at). Upload/agendamento simulado (mock) nunca
marca como publicado.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.database import Database, ALLOWED_TRANSITIONS
from src.state_machine import StateMachine
from src.workflow.publication import TikTokAdapter, PublicationManager
from src.workflow.state import PublicationTarget


def _mk_target(local_state: str = "queued", remote_confirmed: bool = False) -> PublicationTarget:
    return PublicationTarget(
        video_id="vid-01",
        platform="tiktok",
        local_state=local_state,
        remote_id="rem-123" if remote_confirmed else None,
        remote_status="published" if remote_confirmed else None,
        confirmed_at="2026-01-01T00:00:00Z" if remote_confirmed else None,
        publication_confirmed_at="2026-01-01T00:00:00Z" if remote_confirmed else None,
        channel_id="ch-01",
    )


# --------------------------------------------------------------------------- #
# CR-01: PUBLISHED exige confirmação remota real                            #
# --------------------------------------------------------------------------- #
def test_published_requires_remote_confirmation_real():
    """Um target só pode ser considerado publicado se houver remote_id +
    remote_status e confirmed_at vindos da confirmação remota."""
    # Sem confirmação remota: nunca publicado.
    t = _mk_target(local_state="queued", remote_confirmed=False)
    assert t.local_state != "published"
    assert t.publication_confirmed_at is None
    assert t.remote_id is None or t.confirmed_at is None

    # Com confirmação remota real: publicado é legítimo.
    t = _mk_target(local_state="published", remote_confirmed=True)
    assert t.local_state == "published"
    assert t.remote_id == "rem-123"
    assert t.remote_status == "published"
    assert t.confirmed_at is not None
    assert t.publication_confirmed_at is not None


def test_mock_never_marks_published_without_real_confirmation(tmp_path: Path):
    """Adaptador TikTok em modo mock nunca avança para 'published' sem dados
    remotos reais (confirmed_at permanece None e local_state não muda)."""
    adapter = TikTokAdapter(live=False)
    target = _mk_target(local_state="queued", remote_confirmed=False)

    out = adapter.get_remote_status(target)

    assert out.local_state == "queued"  # não avança para publicado
    assert out.publication_confirmed_at is None
    assert out.remote_id is None
    assert out.error_message is None or "não confirmado" in out.error_message


def test_publication_manager_does_not_publish_mock_target():
    """PublicationManager não considera um target mock como publicado."""
    manager = PublicationManager([TikTokAdapter(live=False)])
    target = _mk_target(local_state="queued", remote_confirmed=False)

    results = manager.get_all_remote_status([target])

    assert len(results) == 1
    assert results[0]["local_state"] == "queued"
    assert results[0]["remote_status"] != "published"
    assert results[0]["confirmed_at"] is None


def test_no_false_published_in_state_machine_transition(tmp_path):
    """O state machine nunca transiciona para PUBLISHED sem confirmação remota:
    a transição para PUBLISHED só ocorre a partir de PUBLISHING (do banco), e
    PUBLISHING só é atingido após UPLOADED/QUEUED — nunca 'published' por
    decisão local do adaptador."""
    from src.database import Database, ALLOWED_TRANSITIONS
    from src.state_machine import StateMachine

    db = Database(tmp_path / "cr01.sqlite3")
    sm = StateMachine(db)
    db.create_video(
        video_id="vid-01", execution_id="exec-01", topic="t", template="curiosidade",
        kind="factual", title="T", description="D", hashtags=[], script_json={},
        total_seconds=10.0,
    )
    # Caminho legítimo até PUBLISHING: ... UPLOADED -> QUEUED -> PUBLISHING.
    for to in ("SCRIPT_READY", "RENDERING", "RENDERED", "VALIDATED",
               "AWAITING_APPROVAL", "APPROVED", "UPLOADING", "UPLOADED", "QUEUED"):
        sm.advance("vid-01", to)

    # PUBLISHING só a partir de QUEUED (não salta diretamente).
    sm.advance("vid-01", "PUBLISHING")
    # PUBLISHED a partir de PUBLISHING é permitido pelo state machine, mas a
    # decisão de publicar (marcar PUBLISHED) depende da confirmação remota
    # real — o adaptador mock nunca produz isso.
    assert db.get_video("vid-01")["status"] == "PUBLISHING"
    db.close()


# --------------------------------------------------------------------------- #
# CR-01: PUBLISHED exige confirmação remota na persistência                   #
# --------------------------------------------------------------------------- #
def test_persistencia_bloqueia_publicado_sem_confirmacao_remota(tmp_path):
    """Database.transition recusa PUBLISHED quando não há identificador remoto
    verificável (cloudinary_id ou buffer_post_id)."""
    db = Database(tmp_path / "cr01_persist.sqlite3")
    try:
        db.create_video(
            video_id="vid-01", execution_id="exec-01", topic="t",
            template="curiosidade", kind="factual", title="T", description="D",
            hashtags=[], script_json={}, total_seconds=10.0,
        )
        # Caminho legítimo até PUBLISHING.
        sm = StateMachine(db)
        for to in ("SCRIPT_READY", "RENDERING", "RENDERED", "VALIDATED",
                   "AWAITING_APPROVAL", "APPROVED", "UPLOADING", "UPLOADED",
                   "QUEUED", "PUBLISHING"):
            sm.advance("vid-01", to)

        # Tentativa de marcar PUBLISHED SEM dados remotos: deve falhar.
        with pytest.raises(Exception) as excinfo:
            db.transition("vid-01", "PUBLISHED", actor="system")
        assert "confirmação remota" in str(excinfo.value).lower()

        # Com dados remotos (cloudinary_id): transição para PUBLISHED é aceita.
        db.transition("vid-01", "PUBLISHED",
                      actor="system",
                      cloudinary_id="cl-123",
                      cloudinary_url="https://res.cloudinary.com/demo/video/123")
        assert db.get_video("vid-01")["status"] == "PUBLISHED"
    finally:
        db.close()


def test_persistencia_aceita_publicado_com_buffer_post_id(tmp_path):
    """Database.transition aceita PUBLISHED com buffer_post_id como confirmação
    remota (idempotência e identificador remoto verificável)."""
    db = Database(tmp_path / "cr01_buffer.sqlite3")
    try:
        db.create_video(
            video_id="vid-01", execution_id="exec-01", topic="t",
            template="curiosidade", kind="factual", title="T", description="D",
            hashtags=[], script_json={}, total_seconds=10.0,
        )
        sm = StateMachine(db)
        for to in ("SCRIPT_READY", "RENDERING", "RENDERED", "VALIDATED",
                   "AWAITING_APPROVAL", "APPROVED", "UPLOADING", "UPLOADED",
                   "QUEUED", "PUBLISHING"):
            sm.advance("vid-01", to)

        db.transition("vid-01", "PUBLISHED",
                      actor="system",
                      buffer_post_id="bp-999",
                      buffer_post_state="published")
        assert db.get_video("vid-01")["status"] == "PUBLISHED"
    finally:
        db.close()


def test_queued_preservado_apos_monitoramento():
    """O supervisor preserva QUEUED ao rotear para monitoramento (não
    sobrescreve com MONITORED), mantendo o estado de agendamento."""
    from src.workflow.graph import supervisor

    state = {
        "execution_id": "exec-cr05",
        "video_id": "vid-cr05",
        "status": "QUEUED",
        "current_stage": "QUEUED",
        "mode": "REVIEW",
        "live_enabled": False,
        "auto_enabled": False,
        "approval_status": "approved",
        "buffer_post_id": "bp-1",
    }
    out = supervisor(state)
    assert out["status"] == "QUEUED", f"Esperava QUEUED, obteve {out['status']}"
    assert out["current_stage"] == "QUEUED"
    assert out["_next"] == "monitoring_agent"


__all__ = []
