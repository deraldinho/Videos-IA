"""Regressão: quality_agent propaga settings.video (width/height/fps) para validate_tiktok.

Cobre o Finding 10: o quality_agent de ``src/workflow/graph.py`` deve ler as
dimensões de ``settings.video`` (via ``load_settings``) em vez de usar os
valores hardcoded ``width=1080, height=1920, fps=30.0``.

Como o ``quality_agent`` importa ``load_settings`` e ``FFprobeValidator``
dentro da função, os testes mockam ``src.settings.load_settings`` e o método
``FFprobeValidator.validate_tiktok`` para capturar os argumentos recebidos.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from src.settings import QualitySettings
from src.workflow.graph import quality_agent


def _state_com_transcrito(mp4_path: str) -> dict:
    """Estado mínimo com um MP4 pronto para validação de qualidade."""
    return {
        "execution_id": "exec-q",
        "video_id": "vid-q",
        "topic": "tema de teste",
        "status": "TRANSCRIBED",
        "current_stage": "TRANSCRIBED",
        "video_assets": [{"mp4_path": mp4_path}],
    }


def _fake_settings(width: int, height: int, fps: int):
    return SimpleNamespace(
        video=SimpleNamespace(width=width, height=height, fps=fps),
        quality=QualitySettings(),
    )


def _fake_info(width: int, height: int, fps: float):
    return SimpleNamespace(
        width=width,
        height=height,
        fps=fps,
        duration_seconds=2.0,
        has_audio=True,
    )


# --------------------------------------------------------------------------- #
# 1. Dimensões personalizadas (720x1280@24fps) são repassadas a validate_tiktok
# --------------------------------------------------------------------------- #
@patch("src.video.validator.FFprobeValidator.validate_tiktok")
@patch("src.settings.load_settings")
def test_quality_agent_passa_dimensoes_personalizadas(
    mock_load_settings, mock_validate, tmp_path
):
    mp4 = tmp_path / "video.mp4"
    mp4.write_bytes(b"fake mp4")

    mock_load_settings.return_value = _fake_settings(720, 1280, 24)
    mock_validate.return_value = _fake_info(720, 1280, 24.0)

    state = _state_com_transcrito(str(mp4))
    out = quality_agent(state)

    assert out["validation_results"]["valid"] is True

    _, kwargs = mock_validate.call_args
    assert kwargs["width"] == 720
    assert kwargs["height"] == 1280
    assert kwargs["fps"] == 24.0
    assert kwargs["require_audio"] is True
    assert kwargs["strict"] is False  # quality_agent do grafo permanece permissivo (baseline)


# --------------------------------------------------------------------------- #
# 2. Configurações padrão usam 1080x1920@30fps
# --------------------------------------------------------------------------- #
@patch("src.video.validator.FFprobeValidator.validate_tiktok")
@patch("src.settings.load_settings")
def test_quality_agent_usa_dimensoes_padrao(mock_load_settings, mock_validate, tmp_path):
    mp4 = tmp_path / "video.mp4"
    mp4.write_bytes(b"fake mp4")

    mock_load_settings.return_value = _fake_settings(1080, 1920, 30)
    mock_validate.return_value = _fake_info(1080, 1920, 30.0)

    state = _state_com_transcrito(str(mp4))
    out = quality_agent(state)

    assert out["validation_results"]["valid"] is True

    _, kwargs = mock_validate.call_args
    assert kwargs["width"] == 1080
    assert kwargs["height"] == 1920
    assert kwargs["fps"] == 30.0


# --------------------------------------------------------------------------- #
# 3. Sem MP4 => invalido com "Nenhum MP4 para validar" (sem chamar load_settings)
# --------------------------------------------------------------------------- #
def test_quality_agent_sem_mp4_nao_valida():
    state = {
        "execution_id": "exec-q",
        "video_id": "vid-q",
        "topic": "tema",
        "status": "TRANSCRIBED",
        "current_stage": "TRANSCRIBED",
        # sem video_assets
    }
    out = quality_agent(state)

    assert out["validation_results"] == {
        "valid": False,
        "errors": ["Nenhum MP4 para validar"],
    }
