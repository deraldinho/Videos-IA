"""Regressão — HTMLRenderer deve usar settings.video (width/height/fps) em vez de hardcoded.

Cobre o Finding 6: HTMLRenderer.__init__ nao aceitava ``settings`` e trazia
valores fixos (fps=30, 1080x1920) em tres pontos:

1. Comando FFmpeg (framerate/``-r``) — usava ``fps = 30``.
2. Viewport do Puppeteer — usava ``width: 1080, height: 1920``.
3. Script de captura — usava ``const fps = 30``.

Quando ``settings`` e fornecido, deve-se usar ``settings.video.width/height/fps``;
quando nao houver config (ou ela estiver indisponível), deve cair para os defaults
iguais aos valores anteriormente hardcoded (1080/1920/30).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.settings import Settings, VideoSettings
from src.video.renderer import HTMLRenderer


# --------------------------------------------------------------------------- #
# Construtor aceita settings e espelha width/height/fps                       #
# --------------------------------------------------------------------------- #
def test_constructor_uses_settings_dimensions():
    """Com settings custom, espelha width/height/fps do config."""
    settings = Settings(video=VideoSettings(width=720, height=1280, fps=24))
    renderer = HTMLRenderer(output_dir="output/drafts", settings=settings)

    assert renderer.settings is settings
    assert renderer.video_width == 720
    assert renderer.video_height == 1280
    assert renderer.video_fps == 24


def test_constructor_defaults_when_settings_is_none(monkeypatch):
    """Sem settings e sem config disponível, defaults iguais aos hardcoded (30/1080/1920)."""
    import src.video.renderer as module

    monkeypatch.setattr(module, "load_settings", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("config indisponível")
    ))
    renderer = HTMLRenderer(output_dir="output/drafts", settings=None)

    assert renderer.settings is None
    assert renderer.video_width == 1080
    assert renderer.video_height == 1920
    assert renderer.video_fps == 30


# --------------------------------------------------------------------------- #
# Script de captura usa as dimensoes do settings                              #
# --------------------------------------------------------------------------- #
def test_capture_script_uses_settings_dimensions():
    """_capture_script emite width/height/fps do settings no JS gerado."""
    settings = Settings(video=VideoSettings(width=720, height=1280, fps=24))
    renderer = HTMLRenderer(output_dir="output/drafts", settings=settings)

    js = renderer._capture_script("file:///tmp/rendered.html", 2.0, [])

    assert "width: 720, height: 1280" in js
    assert "const fps = 24;" in js


def test_capture_script_defaults_when_no_settings(monkeypatch):
    """Sem settings, _capture_script emite defaults (1080/1920/30)."""
    import src.video.renderer as module

    monkeypatch.setattr(module, "load_settings", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("config indisponível")
    ))
    renderer = HTMLRenderer(output_dir="output/drafts", settings=None)

    js = renderer._capture_script("file:///tmp/rendered.html", 2.0, [])

    assert "width: 1080, height: 1920" in js
    assert "const fps = 30;" in js


# --------------------------------------------------------------------------- #
# FFmpeg (codificacao) usa o fps do settings                                  #
# --------------------------------------------------------------------------- #
def _make_renderer_and_monkeypatch(tmp_path, monkeypatch, settings):
    renderer = HTMLRenderer(output_dir=tmp_path / "out", settings=settings)

    html_path = tmp_path / "rendered.html"
    html_path.write_text("<html></html>", encoding="utf-8")

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        env = kwargs.get("env", {}) or {}
        frames_dir = env.get("FRAMES_DIR")
        # Chamada do Node (capture script): simula frames capturados para que
        # o FFmpeg prossiga. Chamadas do FFmpeg nao gravam frames.
        if frames_dir and cmd and cmd[0] == "node":
            Path(frames_dir).mkdir(parents=True, exist_ok=True)
            (Path(frames_dir) / "frame-0001.png").write_bytes(b"fake")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(renderer, "_find_node", lambda: "node")
    monkeypatch.setattr(renderer, "_find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(renderer, "_find_chromium", lambda: "chromium")

    output_path = tmp_path / "out" / "render.mp4"
    renderer._render_with_browser(
        html_path, {}, 2.0, output_path, "chromium", "node", "ffmpeg", audio_wav=None
    )
    return renderer, calls, output_path


def _capture_encode_cmd(calls):
    ffmpeg_cmds = [c for c in calls if c and c[0] == "ffmpeg"]
    encode_cmds = [c for c in ffmpeg_cmds if "-framerate" in c]
    assert encode_cmds, f"Nenhum comando FFmpeg de codificacao capturado. Comandos: {calls}"
    return encode_cmds[0]


def test_render_with_browser_ffmpeg_uses_settings_fps(tmp_path, monkeypatch):
    """_render_with_browser monta o comando FFmpeg com o fps do settings."""
    settings = Settings(video=VideoSettings(width=720, height=1280, fps=24))
    _, calls, _ = _make_renderer_and_monkeypatch(tmp_path, monkeypatch, settings)

    cmd = _capture_encode_cmd(calls)
    assert cmd[cmd.index("-framerate") + 1] == "24", (
        f"Esperava -framerate 24 (settings.video.fps). Comando: {cmd}"
    )
    assert cmd[cmd.index("-r") + 1] == "24", (
        f"Esperava -r 24 (settings.video.fps). Comando: {cmd}"
    )


def test_render_with_browser_ffmpeg_defaults_when_no_settings(tmp_path, monkeypatch):
    """Sem settings, _render_with_browser usa fps default 30 no FFmpeg."""
    import src.video.renderer as module

    monkeypatch.setattr(module, "load_settings", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("config indisponível")
    ))
    settings = None
    _, calls, _ = _make_renderer_and_monkeypatch(tmp_path, monkeypatch, settings)

    cmd = _capture_encode_cmd(calls)
    assert cmd[cmd.index("-framerate") + 1] == "30", (
        f"Esperava -framerate 30 (default). Comando: {cmd}"
    )


__all__ = []
