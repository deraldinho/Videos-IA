"""Testes de regressão para a unificação de contratos de estado/retentativas/falhas (CP P1 Tarefa 1).

Garante que todos os módulos do projeto usam as mesmas definições unificadas
de VIDEO_STATES, TERMINAL_STATES, ALLOWED_TRANSITIONS, STAGE_ORDER e MAX_RETRIES,
definidas em src.contracts, evitando definições duplicadas e inconsistentes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.contracts import (  # noqa: E402
    ALLOWED_TRANSITIONS,
    DB_STATE_ORDER,
    MAX_RETRIES,
    TERMINAL_STATES,
    VIDEO_STATES,
    Stage,
)
from src.database import (  # noqa: E402
    ALLOWED_TRANSITIONS as DB_ALLOWED_TRANSITIONS,
    TERMINAL_STATES as DB_TERMINAL_STATES,
    VIDEO_STATES as DB_VIDEO_STATES,
)
from src.workflow.state import (  # noqa: E402
    VIDEO_STATES as WS_VIDEO_STATES,
    Stage as WS_Stage,
)
from src.workflow.graph import MAX_RETRIES as GRAPH_MAX_RETRIES  # noqa: E402
from src.workflow.agents.base import BaseAgent  # noqa: E402
from src.state_machine import StateMachine  # noqa: E402
from src.database import Database  # noqa: E402


class _ConcreteAgent(BaseAgent):
    def run(self, state):  # noqa: D401
        return state


# --------------------------------------------------------------------------- #
# VIDEO_STATES unificado                                                      #
# --------------------------------------------------------------------------- #
def test_video_states_unificado_across_modules():
    """Todos os módulos expõem o mesmo conjunto VIDEO_STATES."""
    assert DB_VIDEO_STATES == VIDEO_STATES
    assert WS_VIDEO_STATES == VIDEO_STATES


def test_video_states_contem_estados_esperados():
    """O conjunto unificado contem todos os estados esperados do pipeline."""
    esperados = {
        "IDEA_CREATED", "SCRIPT_READY", "RENDERING", "RENDERED", "VALIDATED",
        "AWAITING_APPROVAL", "APPROVED", "UPLOADING", "UPLOADED", "QUEUED",
        "PUBLISHING", "PUBLISHED", "ACTION_REQUIRED", "FAILED",
    }
    assert VIDEO_STATES == esperados


# --------------------------------------------------------------------------- #
# TERMINAL_STATES unificado                                                   #
# --------------------------------------------------------------------------- #
def test_terminal_states_unificado():
    assert DB_TERMINAL_STATES == TERMINAL_STATES
    assert TERMINAL_STATES == {"PUBLISHED", "FAILED", "ACTION_REQUIRED"}


# --------------------------------------------------------------------------- #
# ALLOWED_TRANSITIONS unificado                                               #
# --------------------------------------------------------------------------- #
def test_allowed_transitions_unificado():
    """ALLOWED_TRANSITIONS do banco é idêntico ao do módulo contracts."""
    assert DB_ALLOWED_TRANSITIONS == ALLOWED_TRANSITIONS


def test_allowed_transitions_nao_permite_saltos_invalidos():
    """APPROVED não pode saltar diretamente para UPLOADED (CR-01/CP-02)."""
    assert "UPLOADED" not in ALLOWED_TRANSITIONS["APPROVED"]
    assert "UPLOADING" in ALLOWED_TRANSITIONS["APPROVED"]


# --------------------------------------------------------------------------- #
# Stage enum unificado                                                        #
# --------------------------------------------------------------------------- #
def test_stage_enum_valores_coincidem_com_video_states():
    """Os valores do enum Stage coincidem com VIDEO_STATES."""
    valores_stage = {s.value for s in Stage}
    assert valores_stage == VIDEO_STATES


def test_workflow_state_stage_eh_o_mesmo_enum():
    """O Stage importado de workflow.state é o mesmo de contracts."""
    assert WS_Stage is Stage


# --------------------------------------------------------------------------- #
# MAX_RETRIES unificado                                                       #
# --------------------------------------------------------------------------- #
def test_max_retries_unificado():
    """O limite de retentativas é o mesmo em graph.py e contracts."""
    assert GRAPH_MAX_RETRIES == MAX_RETRIES == 3


def test_base_agent_max_retries_padrao_igual():
    """O BaseAgent usa o mesmo MAX_RETRIES padrão."""
    assert _ConcreteAgent("x").max_retries == MAX_RETRIES == 3


# --------------------------------------------------------------------------- #
# DB_STATE_ORDER                                                              #
# --------------------------------------------------------------------------- #
def test_db_state_order_eh_subconjunto_valido():
    """Cada estado em DB_STATE_ORDER pertence a VIDEO_STATES."""
    assert set(DB_STATE_ORDER) <= VIDEO_STATES


# --------------------------------------------------------------------------- #
# StateMachine e Database usam as definicoes unificadas                       #
# --------------------------------------------------------------------------- #
def test_state_machine_usa_contracts(tmp_path):
    """StateMachine.validate_state e can_transition usam VIDEO_STATES/ALLOWED_TRANSITIONS unificados."""
    db = Database(tmp_path / "t.sqlite3")
    sm = StateMachine(db)
    try:
        # Estado válido não levanta.
        sm.validate_state("APPROVED")
        # Estado inválido levanta.
        with pytest.raises(Exception):
            sm.validate_state("ESTADO_INEXISTENTE")
        # Transição bloqueada.
        assert sm.can_transition("APPROVED", "UPLOADED") is False
        assert sm.can_transition("APPROVED", "UPLOADING") is True
    finally:
        db.close()
