"""Testes de banco de dados e máquina de estados."""

from __future__ import annotations

import json
from datetime import datetime, timezone, time
from pathlib import Path

import pytest

from src.database import ALLOWED_TRANSITIONS, Database, VIDEO_STATES
from src.errors import DuplicateError, PipelineError
from src.state_machine import StateMachine, TransitionResult


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_video_states_nonempty() -> None:
    assert "IDEA_CREATED" in VIDEO_STATES
    assert "PUBLISHED" in VIDEO_STATES
    assert "FAILED" in VIDEO_STATES


def test_allowed_transitions_block_invalid() -> None:
    assert "SCRIPT_READY" in ALLOWED_TRANSITIONS["IDEA_CREATED"]
    assert "PUBLISHED" not in ALLOWED_TRANSITIONS["IDEA_CREATED"]


def test_database_creates_tables(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    row = db.get_video("nao-existe")
    assert row is None
    db.close()


def test_create_and_transition_video(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    sm = StateMachine(db)

    # Vídeo não existe: advance deve falhar
    with pytest.raises(PipelineError, match="não encontrado"):
        sm.advance("v1", "SCRIPT_READY", actor="test", note="roteiro gerado")

    # Criar primeiro via banco direto (simulando generate)
    db.create_video(
        video_id="v1",
        execution_id="exec-1",
        topic="curiosidade",
        template="curiosidade",
        kind="factual",
        title="Título",
        description="Descrição",
        hashtags=["#tech", "#curiosidade"],
        script_json={"scenes": []},
        total_seconds=45.0,
    )

    # Transições válidas em sequência
    res = sm.advance("v1", "SCRIPT_READY", actor="test")
    assert res.changed is True
    assert res.from_status == "IDEA_CREATED"
    assert res.to_status == "SCRIPT_READY"

    res = sm.advance("v1", "RENDERING", actor="test")
    assert res.changed is True
    assert res.from_status == "SCRIPT_READY"
    assert res.to_status == "RENDERING"

    row = db.get_video("v1")
    assert row["status"] == "RENDERING"

    # Transição inválida: RENDERING -> APPROVED (salta etapas)
    with pytest.raises(PipelineError, match="não permitida"):
        sm.advance("v1", "APPROVED", actor="test")

    # Avançar corretamente até APPROVED
    sm.advance("v1", "RENDERED", actor="test")
    sm.advance("v1", "VALIDATED", actor="test")
    sm.advance("v1", "AWAITING_APPROVAL", actor="test")
    sm.advance("v1", "APPROVED", actor="test")

    # Idempotência: já em APPROVED, sem campos -> changed=False
    res = sm.advance("v1", "APPROVED", actor="test")
    assert res.changed is False

    # Duplicate na criação
    with pytest.raises(DuplicateError):
        db.create_video(
            video_id="v1",
            execution_id="exec-1",
            topic="x",
            template="curiosidade",
            kind="factual",
            title="T",
            description="D",
            hashtags=[],
            script_json={},
            total_seconds=10.0,
        )

    db.close()


def test_record_error_and_clear(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    sm = StateMachine(db)
    db.create_video(
        video_id="v2",
        execution_id="exec-2",
        topic="t",
        template="curiosidade",
        kind="factual",
        title="T",
        description="D",
        hashtags=[],
        script_json={},
        total_seconds=10.0,
    )
    sm.fail("v2", "ffmpeg falhou", actor="test")
    row = db.get_video("v2")
    assert row["status"] == "FAILED"
    assert row["error_message"] == "ffmpeg falhou"
    assert row["retry_count"] == 1

    db.clear_error("v2")
    row = db.get_video("v2")
    assert row["error_message"] is None
    db.close()


def test_scheduled_post_upsert_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "test.sqlite3")
    db.create_video(
        video_id="v3",
        execution_id="exec-3",
        topic="t",
        template="curiosidade",
        kind="factual",
        title="T",
        description="D",
        hashtags=[],
        script_json={},
        total_seconds=10.0,
    )
    db.upsert_scheduled_post(
        video_id="v3",
        buffer_post_id="bp-1",
        state="QUEUED",
        scheduled_at=_utc_iso(),
        platform="tiktok",
    )
    row = db.get_scheduled_post("bp-1")
    assert row is not None
    assert row["state"] == "QUEUED"
    assert row["video_id"] == "v3"

    # Atualizar estado (idempotente por buffer_post_id)
    db.upsert_scheduled_post(
        video_id="v3",
        buffer_post_id="bp-1",
        state="PUBLISHED",
        scheduled_at=_utc_iso(),
    )
    row = db.get_scheduled_post("bp-1")
    assert row["state"] == "PUBLISHED"
    # Deve continuar sendo uma única linha
    with db.transaction() as conn:
        count = conn.execute(
            "SELECT COUNT(*) as c FROM scheduled_posts WHERE buffer_post_id = ?",
            ("bp-1",),
        ).fetchone()["c"]
    assert count == 1
    db.close()


__all__ = []
