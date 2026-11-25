"""Contratos unificados de estados, estágios, transições e retentativas.

Este módulo é a fonte única de verdade para os contratos de estado do pipeline
de vídeo. Todos os módulos que precisam dessas definições devem importar daqui,
evitando definições duplicadas e inconsistentes entre:

- src/database.py (VIDEO_STATES, TERMINAL_STATES, ALLOWED_TRANSITIONS)
- src/workflow/state.py (VIDEO_STATES, Stage)
- src/workflow/graph.py (MAX_RETRIES)
- src/workflow/agents/base.py (max_retries padrão)
- src/graph_orchestrator.py (_DB_STATE_ORDER)
- src/server.py, src/dashboard.py (VIDEO_STATES)

As definições aqui são intencionalmente puras (sem dependências de workflow ou
banco de dados) para que nenhum módulo de baixo nível dependa de um de alto
nível.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping

# Estados válidos do pipeline, em ordem de progresso.
VIDEO_STATES: frozenset[str] = frozenset(
    {
        "IDEA_CREATED",
        "SCRIPT_READY",
        "RENDERING",
        "RENDERED",
        "VALIDATED",
        "AWAITING_APPROVAL",
        "APPROVED",
        "UPLOADING",
        "UPLOADED",
        "QUEUED",
        "PUBLISHING",
        "PUBLISHED",
        "ACTION_REQUIRED",
        "FAILED",
    }
)

# Estados terminais: um vídeo neles nunca é considerado estagnado, mesmo com
# updated_at antigo (já atingiu um desfecho: publicado, falhou ou requer ação).
TERMINAL_STATES: frozenset[str] = frozenset({"PUBLISHED", "FAILED", "ACTION_REQUIRED"})

# Transições permitidas: origem -> conjunto de destinos possíveis.
# Aplicado por StateMachine.can_transition/advance e Database.transition.
ALLOWED_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "IDEA_CREATED": {"SCRIPT_READY", "FAILED"},
    "SCRIPT_READY": {"RENDERING", "FAILED"},
    "RENDERING": {"RENDERED", "FAILED"},
    "RENDERED": {"VALIDATED", "FAILED"},
    "VALIDATED": {"AWAITING_APPROVAL", "FAILED"},
    "AWAITING_APPROVAL": {"APPROVED", "FAILED", "ACTION_REQUIRED"},
    "APPROVED": {"UPLOADING", "FAILED"},
    "UPLOADING": {"UPLOADED", "FAILED"},
    "UPLOADED": {"QUEUED", "FAILED"},
    "QUEUED": {"PUBLISHING", "FAILED"},
    "PUBLISHING": {"PUBLISHED", "ACTION_REQUIRED", "FAILED"},
    "PUBLISHED": frozenset(),  # terminal
    "ACTION_REQUIRED": {"PUBLISHING", "FAILED"},
    "FAILED": {"RENDERING", "UPLOADING", "QUEUED", "PUBLISHING"},  # retomável
}

# Ordem linear dos estados do banco (para reconstruir caminhos válidos quando o
# grafo salta estados intermediários).
DB_STATE_ORDER = [
    "IDEA_CREATED",
    "SCRIPT_READY",
    "RENDERING",
    "RENDERED",
    "VALIDATED",
    "AWAITING_APPROVAL",
    "APPROVED",
    "UPLOADING",
    "UPLOADED",
    "QUEUED",
    "PUBLISHING",
    "PUBLISHED",
    "ACTION_REQUIRED",
    "FAILED",
]

# Limite de retentativas por estágio antes de encerrar com erro.
MAX_RETRIES = 3


class Stage(str, Enum):
    """Estágios do pipeline (ordenados)."""

    IDEA_CREATED = "IDEA_CREATED"
    SCRIPT_READY = "SCRIPT_READY"
    RENDERING = "RENDERING"
    RENDERED = "RENDERED"
    VALIDATED = "VALIDATED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"
    QUEUED = "QUEUED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    FAILED = "FAILED"


__all__ = [
    "VIDEO_STATES",
    "TERMINAL_STATES",
    "ALLOWED_TRANSITIONS",
    "DB_STATE_ORDER",
    "MAX_RETRIES",
    "Stage",
]
