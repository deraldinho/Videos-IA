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
from pathlib import Path

import pytest

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
