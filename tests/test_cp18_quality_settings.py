"""CP18 — Quality Gates com limites configuráveis via settings.

Valida que os limiares dos quality gates (``strict=True`` em
``src/video/validator.py``) passam a ser lidos de ``config.yaml`` através de
``src/settings.Settings``, em vez de permanecerem hardcoded.

Cenários (TDD: RED → patch → GREEN):
1. ``QualitySettings`` é lido da seção ``quality`` do config.yaml.
2. ``Settings`` expõe ``.quality`` com defaults iguais aos atuais de
   ``QualityThresholds`` (sem alterar comportamento por omissão).
3. Limiares customizados do settings afetam a validação (ex.: um
   ``max_black_ratio`` mais rígido rejeita um vídeo que o default aceita).
4. O orquestrador passa thresholds/strict derivados do settings em
   ``_stage_validate``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from src.errors import PipelineError
from src.settings import QualitySettings, Settings, VideoSettings, load_settings
from src.video.validator import FFprobeValidator, QualityThresholds

# --------------------------------------------------------------------------- #
# Helpers — geração de MP4 de teste com FFmpeg                                #
# --------------------------------------------------------------------------- #

_RESOLUTION = "1080x1920"
_FPS = 30

_FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


def _run_ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", *args], capture_output=True, check=True)


def make_black_transition(path: Path) -> None:
    """Vídeo com 0.5s de preto em 2s -> ratio 0.25 (aceito pelo default 0.50)."""
    _run_ffmpeg([
        "-f", "lavfi", "-i", f"color=c=red:s={_RESOLUTION}:r={_FPS}:d=1",
        "-f", "lavfi", "-i", f"color=c=black:s={_RESOLUTION}:r={_FPS}:d=0.5",
        "-f", "lavfi", "-i", f"color=c=red:s={_RESOLUTION}:r={_FPS}:d=0.5",
        "-f", "lavfi", "-i", f"sine=frequency=440:d=2",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p[v]",
        "-map", "[v]", "-map", "3:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(_FPS),
        "-c:a", "aac", "-shortest", str(path),
    ])


# --------------------------------------------------------------------------- #
# 1. QualitySettings lido da seção quality do config.yaml                     #
# --------------------------------------------------------------------------- #
def test_quality_settings_lido_do_config_yaml(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mode: REVIEW\nniche: teste\n"
        "quality:\n"
        "  enabled: true\n"
        "  strict: true\n"
        "  max_black_ratio: 0.2\n"
        "  max_continuous_black_seconds: 1.0\n"
        "  min_mean_volume_db: -40.0\n"
        "  dynamic_scene_samples: 3\n"
        "  dynamic_scene_thumbnail_size: 80\n"
        "  dynamic_scene_phash_threshold: 0.7\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")

    settings = load_settings(cfg, env)
    q = settings.quality
    assert q.enabled is True
    assert q.strict is True
    assert q.max_black_ratio == 0.2
    assert q.max_continuous_black_seconds == 1.0
    assert q.min_mean_volume_db == -40.0
    assert q.dynamic_scene_samples == 3
    assert q.dynamic_scene_thumbnail_size == 80
    assert q.dynamic_scene_phash_threshold == 0.7


# --------------------------------------------------------------------------- #
# 2. Defaults preservados quando a seção quality está ausente                 #
# --------------------------------------------------------------------------- #
def test_quality_settings_defaults_iguais_aos_hardcoded(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("mode: REVIEW\nniche: teste\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")

    settings = load_settings(cfg, env)
    q = settings.quality

    # Devem bater com os defaults atuais de QualityThresholds (sem regressão).
    default_thresholds = QualityThresholds()
    assert q.max_black_ratio == default_thresholds.max_black_ratio == 0.50
    assert q.max_continuous_black_seconds == default_thresholds.max_continuous_black_seconds == 3.0
    assert q.min_mean_volume_db == default_thresholds.min_mean_volume_db == -60.0
    assert q.dynamic_scene_samples == default_thresholds.dynamic_scene_samples == 5
    assert q.dynamic_scene_thumbnail_size == default_thresholds.dynamic_scene_thumbnail_size == 160
    assert q.dynamic_scene_phash_threshold == default_thresholds.dynamic_scene_phash_threshold == 0.9


# --------------------------------------------------------------------------- #
# 3. Limiares customizados do settings afetam a validação                     #
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not _FFMPEG_AVAILABLE, reason="ffmpeg não disponível no ambiente")
def test_validator_usa_thresholds_do_settings_rejeita_mais_rigido(tmp_path: Path) -> None:
    """max_black_ratio=0.1 do settings rejeita o vídeo de transição (ratio 0.25)
    que o default (0.50) aceita — prova que os limiares vêm do settings."""
    video = tmp_path / "transition.mp4"
    make_black_transition(video)

    settings = Settings(
        video=VideoSettings(),
        quality=QualitySettings(max_black_ratio=0.1, strict=True),
    )
    thresholds = settings.quality.to_thresholds()
    validator = FFprobeValidator(thresholds=thresholds)

    # Default (0.50) aceita; settings (0.1) rejeita.
    FFprobeValidator().validate_tiktok(video, strict=True)  # default: aceita
    with pytest.raises(PipelineError, match="pretos"):
        validator.validate_tiktok(video, strict=True)


# --------------------------------------------------------------------------- #
# 4. Orquestrador passa thresholds/strict do settings em _stage_validate     #
# --------------------------------------------------------------------------- #
def test_orchestrator_validate_passa_settings_quality(tmp_path: Path) -> None:
    from src.orchestrator import Orchestrator

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mode: REVIEW\nniche: teste\n"
        "quality:\n  max_black_ratio: 0.1\n  strict: true\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    settings = load_settings(cfg, env)

    db_path = tmp_path / "v.sqlite3"
    orch = Orchestrator(settings=settings, db_path=db_path)
    try:
        # self.validator deve existir e usar thresholds do settings.
        assert orch.validator is not None
        # Os thresholds do validador refletem max_black_ratio=0.1 do settings.
        assert orch.validator.thresholds.max_black_ratio == 0.1
        # O strict deriva do settings (True aqui).
        assert settings.quality.strict is True
    finally:
        orch.close()
