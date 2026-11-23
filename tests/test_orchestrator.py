"""Testes para o Orchestrator (pipeline etapa a etapa)."""

from __future__ import annotations

from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest

from src.database import Database
from src.errors import PipelineError, DuplicateError
from src.orchestrator import Orchestrator, STAGE_ORDER
from src.settings import load_settings


def _tmp_settings(tmp_path: Path):
    """Cria settings em modo REVIEW com DB temporário."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mode: REVIEW\nniche: teste\nscript_kind: factual\n"
        "video:\n  min_seconds: 30\n  max_seconds: 60\n  target_seconds: 45\n  ffmpeg: ffmpeg\n  ffprobe: ffprobe\n  node: node\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    return load_settings(cfg, env)


def test_stage_order_is_deterministic():
    assert STAGE_ORDER == ["script", "tts", "render", "validate", "approve", "upload", "publish"]


def test_orchestrator_init_creates_db():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "videos.sqlite3"
        orch = Orchestrator(settings=_tmp_settings(Path(td)), db_path=db_path)
        assert orch.db is not None
        assert orch.sm is not None
        orch.close()


def test_orchestrator_passa_settings_para_hyperframes_adapter():
    """O Orchestrator deve repassar `settings` ao HyperFramesAdapter.

    Sem isso o adapter cai nos defaults hardcoded (1080/1920/30) ignorando
    width/height/fps do config.yaml (Finding 4 ligado ao pipeline real).
    """
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "videos.sqlite3"
        cfg = Path(td) / "config.yaml"
        cfg.write_text(
            "mode: REVIEW\nniche: teste\nscript_kind: factual\n"
            "video:\n  min_seconds: 30\n  max_seconds: 60\n  target_seconds: 45\n"
            "  width: 720\n  height: 1280\n  fps: 24\n  ffmpeg: ffmpeg\n  ffprobe: ffprobe\n  node: node\n",
            encoding="utf-8",
        )
        env = Path(td) / ".env"
        env.write_text("", encoding="utf-8")
        settings = load_settings(cfg, env)

        with patch("src.orchestrator.HyperFramesAdapter") as mock_adapter:
            orch = Orchestrator(settings=settings, db_path=db_path)
            assert mock_adapter.called
            kwargs = mock_adapter.call_args.kwargs
            assert "settings" in kwargs, "HyperFramesAdapter deve receber settings"
            assert kwargs["settings"] is settings
            assert kwargs["settings"].video.width == 720
            assert kwargs["settings"].video.height == 1280
            assert kwargs["settings"].video.fps == 24
        orch.close()


def test_generate_creates_video_in_idea_created():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "videos.sqlite3"
        orch = Orchestrator(settings=_tmp_settings(Path(td)), db_path=db_path)
        try:
            # Modo assisted sem response do operador: deve criar vídeo e ficar pendente.
            res = orch.generate(execution_id="exec-teste-1", topic="teste de tópico")
            assert res.video_id == "exec-teste-1"
            assert res.stage == "script"
            assert res.status == "SCRIPT_READY"
            # O vídeo existe no banco.
            row = orch.db.get_video("exec-teste-1")
            assert row is not None
            assert row["status"] == "SCRIPT_READY"
            assert row["topic"] == "teste de tópico"
        finally:
            orch.close()


def test_generate_is_idempotent_by_execution_id():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "videos.sqlite3"
        orch = Orchestrator(settings=_tmp_settings(Path(td)), db_path=db_path)
        try:
            res1 = orch.generate(execution_id="exec-idem", topic="tópico A")
            res2 = orch.generate(execution_id="exec-idem", topic="tópico B")
            assert res1.video_id == res2.video_id == "exec-idem"
            # Não deve duplicar.
            rows = orch.db.list_by_status("SCRIPT_READY")
            ids = [r["video_id"] for r in rows]
            assert ids.count("exec-idem") == 1
        finally:
            orch.close()


def test_run_stage_unknown_raises():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "videos.sqlite3"
        orch = Orchestrator(settings=_tmp_settings(Path(td)), db_path=db_path)
        try:
            # Cria um vídeo para que a falha seja "etapa desconhecida", não "não encontrado".
            orch.db.create_video(
                video_id="v1",
                execution_id="exec-1",
                topic="t",
                template="curiosidade",
                kind="factual",
                title="T",
                description="D",
                hashtags=[],
                script_json={},
                total_seconds=10.0,
            )
            with pytest.raises(PipelineError, match="desconhecida"):
                orch.run_stage("v1", "etapa_inexistente")
        finally:
            orch.close()


def test_status_returns_none_for_missing_video():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "videos.sqlite3"
        orch = Orchestrator(settings=_tmp_settings(Path(td)), db_path=db_path)
        try:
            assert orch.status("nao-existe") is None
        finally:
            orch.close()


def _tmp_settings_video(
    tmp_path: Path, width: int = 720, height: int = 1280, fps: int = 24
):
    """Cria settings em modo REVIEW com dimensoes de video customizadas."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mode: REVIEW\nniche: teste\nscript_kind: factual\n"
        f"video:\n  min_seconds: 30\n  max_seconds: 60\n  target_seconds: 45\n  width: {width}\n"
        f"  height: {height}\n  fps: {fps}\n  ffmpeg: ffmpeg\n  ffprobe: ffprobe\n  node: node\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    return load_settings(cfg, env)


def test_orchestrator_hyperframes_adapter_recebe_settings(tmp_path):
    """Orchestrator deve repassar settings ao HyperFramesAdapter (Finding 8/D)."""
    settings = _tmp_settings_video(tmp_path)
    orch = Orchestrator(settings=settings, db_path=tmp_path / "db.sqlite3")
    try:
        assert orch.hyperframes_adapter.settings is settings
        assert orch.hyperframes_adapter.settings.video.width == 720
    finally:
        orch.close()


def test_orchestrator_renderer_recebe_settings(tmp_path):
    """Orchestrator deve instanciar o HTMLRenderer com settings (Finding 8/E)."""
    settings = _tmp_settings_video(tmp_path)
    orch = Orchestrator(settings=settings, db_path=tmp_path / "db.sqlite3")
    try:
        assert orch.renderer.settings is settings
        assert orch.renderer.video_width == 720
        assert orch.renderer.video_height == 1280
        assert orch.renderer.video_fps == 24
    finally:
        orch.close()


__all__ = []
