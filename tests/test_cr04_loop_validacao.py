"""Testes de regressão CR-04: loop de validação audiovisual.

Defeito: ``quality_agent`` volta para ``SCRIPT_READY`` quando a validação do
MP4 falha, o que faz o supervisor rotear de volta para voice → visual → render
→ transcription → quality → SCRIPT_READY, criando um ciclo infinito até
esgotar as retentativas (MAX_RETRIES) e encerrar em FAILED, em vez de tratar a
falha de qualidade de forma controlada.

Regra: quando a validação de qualidade falhar, o pipeline não deve retomar do
início (SCRIPT_READY) em loop. Deve sinalizar a falha de forma que o supervisor
encerre ou aguarde intervenção, sem ciclo infinito.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.workflow.graph import quality_agent, supervisor
from src.workflow.state import Stage


def _invalid_mp4_state() -> dict:
    return {
        "execution_id": "exec-cr04",
        "video_id": "vid-cr04",
        "status": "TRANSCRIBED",
        "current_stage": "TRANSCRIBED",
        "mode": "REVIEW",
        "live_enabled": False,
        "auto_enabled": False,
        # MP4 inválido / inexistente -> qualidade falha.
        "video_assets": [{"mp4_path": str(Path("nao_existe.mp4"))}],
        "retries": 0,
    }


def test_quality_agent_invalid_mp4_does_not_return_script_ready():
    """quality_agent com MP4 inválido não deve voltar para SCRIPT_READY (loop).

    Deve registrar validation_results com valid=False e não criar um estado que
    force o supervisor a reiniciar o pipeline do roteiro.
    """
    state = _invalid_mp4_state()
    out = quality_agent(state)

    assert out["validation_results"]["valid"] is False
    assert out["validation_results"]["errors"]
    # Não deve voltar para SCRIPT_READY (isso criaria loop voice→...→quality→SCRIPT_READY).
    assert out["status"] != "SCRIPT_READY"


def test_supervisor_after_failed_quality_does_not_loop_to_voice():
    """Quando a qualidade falhou (status não avançou para VALIDATED), o
    supervisor não deve rotear de volta para voice_agent em um ciclo sem fim."""
    state = _invalid_mp4_state()
    out = quality_agent(state)
    # Qualidade falhou: status não é VALIDATED.
    assert out["status"] != "VALIDATED"

    # O supervisor, diante de um estado sem transição aplicável (qualidade
    # inválida), não deve rotear para voice_agent (reinício do pipeline).
    nxt = supervisor(out)
    assert nxt["_next"] != "voice_agent"


def test_qualidade_invalida_encerra_em_vez_de_loop_infinito():
    """Quando a validação de qualidade falha, o supervisor encerra (__end__)
    em vez de rotear de volta para voice_agent, evitando ciclo infinito
    voice→visual→render→transcription→quality."""
    state = _invalid_mp4_state()
    out = quality_agent(state)
    assert out["validation_results"]["valid"] is False
    assert out["status"] != Stage.VALIDATED.value

    nxt = supervisor(out)
    # Sem transição aplicável para qualidade inválida -> encerra.
    assert nxt["_next"] == "__end__", (
        f"Esperava __end__ (encerra), obteve _next={nxt['_next']}"
    )


__all__ = []
