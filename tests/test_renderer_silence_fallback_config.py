"""Testes de regressão para o Finding 9.

O caminho de silêncio/placeholder (``_render_placeholder``) do ``HTMLRenderer``
deve propagar ``settings.video`` (width/height/fps) no comando FFmpeg, em vez
de usar os valores hardcoded ``1080x1920:r=30``.

Esses testes exercitam o método diretamente (mockando ``subprocess.run``), uma
vez que ``_render_placeholder`` é um caminho independente (não invocado por
``render`` no fluxo atual).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.settings import Settings, VideoSettings
from src.video.renderer import HTMLRenderer


def _captured_ffmpeg_cmd(renderer: HTMLRenderer, seconds: float = 5.0,
                         ffmpeg: str = "ffmpeg") -> list[str]:
    """Executa ``_render_placeholder`` com ``subprocess.run`` mockado.

    Devolve a lista de argumentos do comando FFmpeg que teria sido executado,
    sem realmente invocar o FFmpeg nem gravar arquivo de saída.
    """
    output_path = Path("/tmp/renderer_silence_fallback.mp4")
    captured: dict[str, list[str]] = {}

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        captured["cmd"] = list(cmd)

        class _Result:
            returncode = 0
            stderr = ""

        return _Result()

    with patch("src.video.renderer.subprocess.run", side_effect=fake_run):
        renderer._render_placeholder(seconds, output_path, ffmpeg)
    return captured.get("cmd", [])


# --------------------------------------------------------------------------- #
# Finding 9 — silêncio/fallback respeita settings.video                       #
# --------------------------------------------------------------------------- #
def test_silence_fallback_uses_custom_settings_dimensions_and_fps():
    """Com settings customizadas (720x1280 @24fps) o fallback deve usá-las."""
    settings = Settings(video=VideoSettings(width=720, height=1280, fps=24))
    renderer = HTMLRenderer(settings=settings)
    cmd = _captured_ffmpeg_cmd(renderer, seconds=4.0)

    assert any(arg.startswith("color=c=black:s=720x1280:r=24:d=") for arg in cmd), cmd


def test_silence_fallback_default_settings_uses_1080x1920_30():
    """Sem settings (None) o fallback deve manter os defaults 1080x1920 @30fps."""
    renderer = HTMLRenderer()  # settings=None -> defaults 1080/1920/30
    cmd = _captured_ffmpeg_cmd(renderer, seconds=4.0)

    assert any(arg.startswith("color=c=black:s=1080x1920:r=30:d=") for arg in cmd), cmd
    r_positions = [i for i, arg in enumerate(cmd) if arg == "-r"]
    assert r_positions, cmd
    assert cmd[r_positions[0] + 1] == "30", cmd


def test_silence_fallback_command_includes_s_and_r_matching_settings():
    """O comando deve conter s={width}x{height} e -r {fps} condizentes com settings."""
    settings = Settings(video=VideoSettings(width=540, height=960, fps=15))
    renderer = HTMLRenderer(settings=settings)
    cmd = _captured_ffmpeg_cmd(renderer, seconds=3.0)

    assert any("s=540x960" in arg for arg in cmd), cmd

    r_positions = [i for i, arg in enumerate(cmd) if arg == "-r"]
    assert r_positions, cmd
    assert cmd[r_positions[0] + 1] == "15", cmd
