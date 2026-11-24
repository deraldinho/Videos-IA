"""Testes de regressão CR-03: detecção de ausência de progresso.

Defeito: não há mecanismo para detectar vídeos que ficam estagnados
(sem avançar de estado) por um período prolongado. Um vídeo pode permanecer
para sempre em um estado não-terminal (ex.: RENDERED, VALIDATED,
AWAITING_APPROVAL) sem que o pipeline sinalize a falta de progresso.

Regra: o banco deve ser capaz de listar vídeos estagnados — estados não-terminais
cujo updated_at é mais antigo que um limite de inatividade configurável.
Vídeos terminais (PUBLISHED, FAILED, ACTION_REQUIRED) nunca são estagnados.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.database import Database


def _make_video(db, video_id: str, status: str = "RENDERED",
                updated_at: str | None = None) -> None:
    db.create_video(
        video_id=video_id,
        execution_id=f"exec-{video_id}",
        topic="t",
        template="curiosidade",
        kind="factual",
        title="T",
        description="D",
        hashtags=[],
        script_json={},
        total_seconds=10.0,
    )
    if updated_at is not None or status != "IDEA_CREATED":
        with db.transaction() as conn:
            conn.execute(
                "UPDATE videos SET status = ?, updated_at = ? WHERE video_id = ?",
                (status, updated_at or datetime.now(timezone.utc).isoformat(), video_id),
            )


# --------------------------------------------------------------------------- #
# CR-03: detecção de ausência de progresso                                    #
# --------------------------------------------------------------------------- #
def test_detect_stalled_video_without_progress(tmp_path):
    """Vídeo em estado não-terminal com updated_at antigo é detectado como estagnado."""
    db = Database(tmp_path / "cr03.sqlite3")
    try:
        antigo = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _make_video(db, "v1", status="RENDERED", updated_at=antigo)

        stalled = db.stalled_videos(max_idle_seconds=3600)
        ids = [r["video_id"] for r in stalled]
        assert "v1" in ids, f"esperava v1 como estagnado, obteve {ids}"
    finally:
        db.close()


def test_recent_video_not_stalled(tmp_path):
    """Vídeo recente (dentro do limite de inatividade) não é estagnado."""
    db = Database(tmp_path / "cr03b.sqlite3")
    try:
        recente = datetime.now(timezone.utc).isoformat()
        _make_video(db, "v2", status="RENDERED", updated_at=recente)

        stalled = db.stalled_videos(max_idle_seconds=3600)
        ids = [r["video_id"] for r in stalled]
        assert "v2" not in ids
    finally:
        db.close()


def test_terminal_video_never_stalled(tmp_path):
    """Vídeo em estado terminal nunca é estagnado, mesmo com updated_at antigo."""
    db = Database(tmp_path / "cr03c.sqlite3")
    try:
        antigo = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        for status in ("PUBLISHED", "FAILED", "ACTION_REQUIRED"):
            _make_video(db, f"v_{status}", status=status, updated_at=antigo)

        stalled = db.stalled_videos(max_idle_seconds=3600)
        ids = [r["video_id"] for r in stalled]
        for status in ("PUBLISHED", "FAILED", "ACTION_REQUIRED"):
            assert f"v_{status}" not in ids, (
                f"{status} é terminal e não deve ser estagnado, obteve {ids}"
            )
    finally:
        db.close()


__all__ = []
