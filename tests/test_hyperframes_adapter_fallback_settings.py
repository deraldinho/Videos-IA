"""Regressao — Finding 8: dimensoes do video devem vir do settings em TODOS
os pontos de criacao de HTMLRenderer, incluindo o fallback do
HyperFramesAdapter e a instancia direta do Orchestrator.

Cobre:
1. HyperFramesAdapter aceita e armazena settings.
2. Fallback HTMLRenderer do HyperFramesAdapter recebe os settings.
3. HyperFramesAdapter sem settings cai nos defaults (1080/1920/30).
4. HTMLRenderer com settings usa width/height/fps do settings.
5. HTMLRenderer sem settings cai nos defaults (1080/1920/30).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.settings import Settings, VideoSettings
from src.video.hyperframes_adapter import HyperFramesAdapter
from src.video.renderer import HTMLRenderer


# --------------------------------------------------------------------------- #
# HyperFramesAdapter                                                          #
# --------------------------------------------------------------------------- #
def test_hyperframes_adapter_aceita_e_armazena_settings(tmp_path):
    settings = Settings(video=VideoSettings(width=720, height=1280, fps=24))
    adapter = HyperFramesAdapter(output_dir=tmp_path / "out", settings=settings)

    assert adapter.settings is settings


def test_hyperframes_adapter_fallback_recebe_settings(tmp_path):
    settings = Settings(video=VideoSettings(width=720, height=1280, fps=24))
    adapter = HyperFramesAdapter(output_dir=tmp_path / "out", settings=settings)

    fallback = adapter._fallback_renderer
    assert fallback.settings is settings
    assert fallback.video_width == 720
    assert fallback.video_height == 1280
    assert fallback.video_fps == 24


def test_hyperframes_adapter_defaults_sem_settings(tmp_path):
    adapter = HyperFramesAdapter(output_dir=tmp_path / "out")
    assert adapter.settings is None

    fallback = adapter._fallback_renderer
    assert fallback.settings is None
    assert fallback.video_width == 1080
    assert fallback.video_height == 1920
    assert fallback.video_fps == 30


# --------------------------------------------------------------------------- #
# HTMLRenderer                                                                #
# --------------------------------------------------------------------------- #
def test_htmlrenderer_usa_dimensoes_settings(tmp_path):
    settings = Settings(video=VideoSettings(width=720, height=1280, fps=24))
    renderer = HTMLRenderer(output_dir=tmp_path / "out", settings=settings)

    assert renderer.settings is settings
    assert renderer.video_width == 720
    assert renderer.video_height == 1280
    assert renderer.video_fps == 24


def test_htmlrenderer_defaults_sem_settings(tmp_path):
    renderer = HTMLRenderer(output_dir=tmp_path / "out")
    assert renderer.settings is None
    assert renderer.video_width == 1080
    assert renderer.video_height == 1920
    assert renderer.video_fps == 30


__all__ = []
