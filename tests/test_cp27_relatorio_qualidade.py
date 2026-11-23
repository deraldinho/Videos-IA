"""Testes TDD para CP27 — Relatório de qualidade do vídeo.

Critérios de aceitação:
- gerar_relatorio_qualidade(state) -> dict com video_id, execution_id, stage,
  status, valid, errors, timestamp.
- Quando validation_results está presente e válido, reporta valid=True.
- Quando validation_results está presente e inválido, reporta valid=False com errors.
- Quando não há validation_results, reporta valid=False com erro descritivo.
- Dict serializável (JSON-amigável).
- Testes isolados, sem rede, sem credenciais.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.workflow.graph import quality_agent
from src.workflow.quality_report import gerar_relatorio_qualidade
from src.workflow.state import empty_state, state_to_dict


def _state_com_validacao(valido: bool, errors: list[str] | None = None) -> dict:
    state = state_to_dict(empty_state("exec-rel", "vid-rel"))
    state["validation_results"] = {
        "valid": valido,
        "errors": errors or [],
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "duration_seconds": 5.0,
        "has_audio": True,
    }
    state["current_stage"] = "VALIDATED"
    state["status"] = "VALIDATED"
    state["video_assets"] = [{"mp4_path": str(Path("video.mp4"))}]
    return state


# --------------------------------------------------------------------------- #
# Relatório quando validação é válida                                         #
# --------------------------------------------------------------------------- #
def test_relatorio_qualidade_valido():
    """Relatório de qualidade para vídeo válido reporta valid=True."""
    state = _state_com_validacao(valido=True)
    rel = gerar_relatorio_qualidade(state)
    assert rel["video_id"] == "vid-rel"
    assert rel["execution_id"] == "exec-rel"
    assert rel["stage"] == "VALIDATED"
    assert rel["status"] == "VALIDATED"
    assert rel["valid"] is True
    assert rel["errors"] == []
    assert "timestamp" in rel


# --------------------------------------------------------------------------- #
# Relatório quando validação é inválida                                       #
# --------------------------------------------------------------------------- #
def test_relatorio_qualidade_invalido_com_erros():
    """Relatório de qualidade para vídeo inválido reporta valid=False e errors."""
    state = _state_com_validacao(
        valido=False, errors=["Resolução inválida: 720x1280", "FPS abaixo do mínimo: 15"]
    )
    rel = gerar_relatorio_qualidade(state)
    assert rel["valid"] is False
    assert len(rel["errors"]) == 2
    assert "Resolução inválida" in rel["errors"][0]
    assert "FPS abaixo do mínimo" in rel["errors"][1]


# --------------------------------------------------------------------------- #
# Relatório sem validation_results                                            #
# --------------------------------------------------------------------------- #
def test_relatorio_qualidade_sem_validacao():
    """Sem validation_results, relatório reporta valid=False com erro descritivo."""
    state = state_to_dict(empty_state("exec-rel2", "vid-rel2"))
    state["current_stage"] = "RENDERED"
    state["status"] = "RENDERED"
    rel = gerar_relatorio_qualidade(state)
    assert rel["valid"] is False
    assert len(rel["errors"]) > 0
    assert "não há resultado de validação" in rel["errors"][0].lower() or \
        "sem resultado de validação" in rel["errors"][0].lower()


# --------------------------------------------------------------------------- #
# Relatório serializável (JSON)                                               #
# --------------------------------------------------------------------------- #
def test_relatorio_qualidade_serializavel_json(tmp_path):
    """Relatório de qualidade pode ser serializado para JSON."""
    state = _state_com_validacao(valido=True)
    rel = gerar_relatorio_qualidade(state)
    json_str = __import__("json").dumps(rel)
    assert "vid-rel" in json_str
    assert "VALIDATED" in json_str
    assert "timestamp" in json_str


# --------------------------------------------------------------------------- #
# Integração com quality_agent (relatório após validação do grafo)            #
# --------------------------------------------------------------------------- #
@patch("src.video.validator.FFprobeValidator.validate_tiktok")
@patch("src.settings.load_settings")
def test_relatorio_apos_quality_agent_valido(mock_load_settings, mock_validate, tmp_path):
    """Após quality_agent com vídeo válido, relatório reflete valid=True."""
    from types import SimpleNamespace

    mock_load_settings.return_value = SimpleNamespace(
        video=SimpleNamespace(width=1080, height=1920, fps=30),
    )
    mock_validate.return_value = SimpleNamespace(
        width=1080, height=1920, fps=30.0, duration_seconds=5.0, has_audio=True,
    )

    state = state_to_dict(empty_state("exec-rel3", "vid-rel3"))
    state["video_assets"] = [{"mp4_path": str(tmp_path / "video.mp4"),
                              "width": 1080, "height": 1920, "fps": 30.0,
                              "duration_seconds": 5.0, "has_audio": True}]
    state["status"] = "RENDERED"
    state["current_stage"] = "RENDERED"

    out = quality_agent(state)
    assert out["validation_results"]["valid"] is True

    rel = gerar_relatorio_qualidade(out)
    assert rel["valid"] is True
    assert rel["video_id"] == "vid-rel3"
    assert rel["stage"] == "VALIDATED"
    assert rel["errors"] == []


__all__ = []
