"""Regressao — RenderAgent deve passar settings ao HTMLRenderer e usar
valores do settings no VideoAsset (Finding 7).

Sem isso o agente mantem hardcoded 1080/1920/30 ignorando
width/height/fps do config.yaml — espelho do Finding 5 (orchestrador ->
HyperFramesAdapter), agora no agente de workflow.

O RenderAgent carrega load_settings() internamente (BaseAgent nao injeta
settings) e repassa settings=self.settings ao HTMLRenderer, além de ler
settings.video.width/height/fps no VideoAsset. Comportamento preservado:
quando config nao sobrescreve, cai para defaults iguais aos hardcoded
(1080/1920/30).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from src.settings import Settings, VideoSettings
from src.workflow.agents.render import RenderAgent
from src.workflow.state import empty_state, state_to_dict


def _initial_state(video_id: str = "vid-render-settings") -> dict:
    state = state_to_dict(empty_state(video_id, video_id))
    state["topic"] = "tema render settings"
    state["status"] = "VISUAL_READY"
    state["current_stage"] = "VISUAL_READY"
    state["storyboard"] = {"template": "curiosidade", "total_seconds": 2.0}
    state["script"] = {
        "title": "T",
        "description": "D",
        "hashtags": ["#ia"],
        "scenes": [{"role": "gancho", "seconds": 2.0, "heading": "H",
                    "narration": ["Olá"], "source_ids": []}],
        "total_seconds": 2.0,
    }
    state["audio_assets"] = []
    return state


def _patch_load_settings(monkeypatch, settings: Settings) -> None:
    import src.workflow.agents.render as module

    monkeypatch.setattr(module, "load_settings", lambda *a, **k: settings)


@contextmanager
def _fake_renderer(tmp_path, monkeypatch):
    """Mocka HTMLRenderer (importado localmente em run) e devolve MP4 falso real."""
    fake_mp4 = tmp_path / "renders" / "vid.mp4"
    fake_mp4.parent.mkdir(parents=True, exist_ok=True)
    fake_mp4.write_bytes(b"fake mp4")

    with patch("src.video.renderer.HTMLRenderer") as mock_renderer:
        mock_instance = mock_renderer.return_value
        mock_instance.render.return_value = fake_mp4
        yield mock_renderer, fake_mp4


# --------------------------------------------------------------------------- #
# Red/Green: settings passado com identidade ao HTMLRenderer                #
# --------------------------------------------------------------------------- #
def test_render_agent_passa_settings_para_htmlrenderer(tmp_path, monkeypatch):
    """RenderAgent deve passar settings (identidade) ao HTMLRenderer."""
    settings = Settings(video=VideoSettings(width=720, height=1280, fps=24))
    _patch_load_settings(monkeypatch, settings)

    with _fake_renderer(tmp_path, monkeypatch) as (mock_renderer, _fake_mp4):
        agent = RenderAgent()
        out = agent.run(_initial_state())

    kwargs = mock_renderer.call_args.kwargs
    assert "settings" in kwargs, "HTMLRenderer deve receber settings"
    assert kwargs["settings"] is agent.settings, "settings deve ser o mesmo objeto"
    assert kwargs["settings"] is settings
    assert kwargs["settings"].video.width == 720
    assert kwargs["settings"].video.height == 1280
    assert kwargs["settings"].video.fps == 24

    assert out["status"] == "RENDERED"
    assert out["current_stage"] == "RENDERED"


# --------------------------------------------------------------------------- #
# Red/Green: VideoAsset recebe width/height/fps do settings (nao hardcoded)  #
# --------------------------------------------------------------------------- #
def test_render_agent_usa_valores_settings_no_video_asset(tmp_path, monkeypatch):
    """VideoAsset deve receber width/height/fps do settings."""
    settings = Settings(video=VideoSettings(width=720, height=1280, fps=24))
    _patch_load_settings(monkeypatch, settings)

    with _fake_renderer(tmp_path, monkeypatch) as (mock_renderer, _fake_mp4):
        agent = RenderAgent()
        out = agent.run(_initial_state())

    assert out["video_assets"], "deve gerar video_assets"
    asset = out["video_assets"][0]
    assert asset["width"] == 720, asset
    assert asset["height"] == 1280, asset
    assert asset["fps"] == 24.0, asset


# --------------------------------------------------------------------------- #
# Defaults preservados quando config nao sobrescreve                         #
# --------------------------------------------------------------------------- #
def test_render_agent_defaults_quando_config_nao_sobrescreve(tmp_path, monkeypatch):
    """Sem valores custom, cai para defaults iguais aos hardcoded (1080/1920/30)."""
    settings = Settings()  # video defaults: width=1080, height=1920, fps=30
    _patch_load_settings(monkeypatch, settings)

    with _fake_renderer(tmp_path, monkeypatch) as (mock_renderer, _fake_mp4):
        agent = RenderAgent()
        out = agent.run(_initial_state())

    asset = out["video_assets"][0]
    assert asset["width"] == 1080, asset
    assert asset["height"] == 1920, asset
    assert asset["fps"] == 30.0, asset
    assert mock_renderer.call_args.kwargs["settings"] is agent.settings
