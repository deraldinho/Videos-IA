"""Regressão — HyperFramesAdapter não deve silenciar falhas do HyperFrames.

Cobre o Finding 1 do code-review: o bloco ``except Exception as exc`` de
``render()`` usava ``bare pass``, não logava a exceção, não encadeava com
``from exc`` nem avisava que caiu no fallback. Viola o contrato da docstring
("Nunca silencia falhas").

Quando o HyperFrames falha, a exceção deve ficar visível nos logs (WARNING/ERROR)
e o fallback para HTMLRenderer deve ser usado e explicitamente registrado,
sem alterar a assinatura pública nem o contrato de retorno de ``render()``.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.settings import Settings, VideoSettings
from src.video.hyperframes_adapter import HyperFramesAdapter


def test_hyperframes_failure_is_logged_and_fallback_used(tmp_path, caplog, monkeypatch):
    """HyperFrames falha -> exceção logada (WARNING/ERROR) e fallback usado com aviso."""
    adapter = HyperFramesAdapter(output_dir=tmp_path / "out")

    # Força HyperFrames "disponível" (_cli_bin não None) mas com falha na renderização.
    adapter._cli_bin = "fake-cli"

    def boom(*args, **kwargs):
        raise RuntimeError("HyperFrames CLI explodiu")

    monkeypatch.setattr(adapter, "_render_with_hyperframes", boom)

    # Espia o fallback: garante que é usado e devolve um caminho previsível.
    fallback_path = tmp_path / "out" / "fallback.mp4"
    fallback_path.write_bytes(b"fake mp4")
    monkeypatch.setattr(
        adapter._fallback_renderer,
        "render",
        lambda *a, **k: fallback_path,
    )

    with caplog.at_level(logging.WARNING, logger="src.video.hyperframes_adapter"):
        result = adapter.render(
            "tpl", {"k": "v"}, duration_seconds=2.0, output_name="out.mp4"
        )

    # (a) A falha do HyperFrames deve ser visível nos logs (não silenciosa).
    hyperframes_logs = [
        rec.getMessage()
        for rec in caplog.records
        if "HyperFrames" in rec.getMessage() and rec.levelno >= logging.WARNING
    ]
    assert hyperframes_logs, (
        "Nenhum log de WARNING/ERROR sobre falha do HyperFrames. "
        f"Registros: {[rec.getMessage() for rec in caplog.records]}"
    )

    # (b) O fallback é usado (resultado do HTMLRenderer) e explicitamente registrado.
    assert result == fallback_path, f"Esperava fallback, obteve: {result}"
    fallback_logs = [
        rec.getMessage()
        for rec in caplog.records
        if "fallback" in rec.getMessage().lower()
        and "HTMLRenderer" in rec.getMessage()
    ]
    assert fallback_logs, (
        "Nenhum log de fallback para HTMLRenderer. "
        f"Registros: {[rec.getMessage() for rec in caplog.records]}"
    )


def test_hyperframes_success_not_logged_as_error(tmp_path, caplog, monkeypatch):
    """HyperFrames com sucesso -> nenhum log de erro, retorna o caminho do HyperFrames."""
    adapter = HyperFramesAdapter(output_dir=tmp_path / "out")
    adapter._cli_bin = "fake-cli"

    success_path = tmp_path / "out" / "hyperframes.mp4"
    success_path.write_bytes(b"real mp4")
    monkeypatch.setattr(
        adapter, "_render_with_hyperframes",
        lambda *a, **k: success_path,
    )

    with caplog.at_level(logging.ERROR, logger="src.video.hyperframes_adapter"):
        result = adapter.render(
            "tpl", {"k": "v"}, duration_seconds=2.0, output_name="out.mp4"
        )

    assert result == success_path
    assert not any(
        rec.levelno >= logging.ERROR for rec in caplog.records
    ), f"Não deve haver log de ERROR em caso de sucesso. Registros: {[rec.getMessage() for rec in caplog.records]}"


def _make_adapter(tmp_path, settings, monkeypatch):
    """Constrói um adapter com um template fake e subprocess.run capturável."""
    vendor_dir = tmp_path / "vendor" / "hyperframes"
    (vendor_dir / "templates").mkdir(parents=True)
    (vendor_dir / "templates" / "tpl.html").write_text(
        "<html>{{k}}</html>", encoding="utf-8"
    )
    adapter = HyperFramesAdapter(
        vendor_dir=vendor_dir,
        output_dir=tmp_path / "out",
        settings=settings,
    )
    adapter._cli_bin = "fake-cli"

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return adapter, captured


def test_render_with_hyperframes_uses_config_dimensions(tmp_path, monkeypatch):
    """_render_with_hyperframes monta o CLI com width/height/fps do settings (config.yaml).

    Deve usar os valores do config.yaml (aqui 720x1280 @24fps) em vez dos
    valores hardcoded "30"/"1080"/"1920".
    """
    settings = Settings(video=VideoSettings(width=720, height=1280, fps=24))
    adapter, captured = _make_adapter(tmp_path, settings, monkeypatch)

    output_path = tmp_path / "out" / "render.mp4"
    output_path.write_bytes(b"fake mp4")

    adapter._render_with_hyperframes(
        "tpl", {"k": "v"}, duration_seconds=2.0, output_path=output_path,
        audio_wav=None,
    )

    cmd = captured["cmd"]
    assert "--fps" in cmd and cmd[cmd.index("--fps") + 1] == "24"
    assert "--width" in cmd and cmd[cmd.index("--width") + 1] == "720"
    assert "--height" in cmd and cmd[cmd.index("--height") + 1] == "1280"


def test_render_with_hyperframes_defaults_when_config_unavailable(tmp_path, monkeypatch):
    """Sem settings/config disponível, usa defaults iguais aos hardcoded (30/1080/1920)."""
    import src.video.hyperframes_adapter as module

    monkeypatch.setattr(module, "load_settings", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("config indisponível")
    ))
    adapter, captured = _make_adapter(tmp_path, None, monkeypatch)

    output_path = tmp_path / "out" / "render.mp4"
    output_path.write_bytes(b"fake mp4")

    adapter._render_with_hyperframes(
        "tpl", {"k": "v"}, duration_seconds=2.0, output_path=output_path,
        audio_wav=None,
    )

    cmd = captured["cmd"]
    assert cmd[cmd.index("--fps") + 1] == "30"
    assert cmd[cmd.index("--width") + 1] == "1080"
    assert cmd[cmd.index("--height") + 1] == "1920"
