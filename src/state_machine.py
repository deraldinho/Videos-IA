"""Máquina de estados com transições validadas e operações idempotentes.

Expõe :class:`StateMachine` que encapsula regras de negócio para avançar um
vídeo entre estados, garantir idempotência e impedir que um erro em uma etapa
faça a publicação avançar incorretamente.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from .database import ALLOWED_TRANSITIONS, Database, VIDEO_STATES
from .errors import DuplicateError, PipelineError


@dataclass(frozen=True)
class TransitionResult:
    video_id: str
    from_status: str
    to_status: str
    changed: bool  # False quando já estava no destino e não houve atualização


class StateMachine:
    """Regras de transição de estado + validações de negócio."""

    def __init__(self, database: Database) -> None:
        self.db = database

    def validate_state(self, status: str) -> None:
        if status not in VIDEO_STATES:
            raise PipelineError(f"Estado inválido: {status}")

    def can_transition(self, from_status: str, to_status: str) -> bool:
        self.validate_state(from_status)
        self.validate_state(to_status)
        return to_status in ALLOWED_TRANSITIONS.get(from_status, frozenset())

    def advance(
        self,
        video_id: str,
        to_status: str,
        *,
        actor: str = "system",
        note: Optional[str] = None,
        **fields,
    ) -> TransitionResult:
        """Avança o vídeo para ``to_status`` com validação de transição.

        Idempotente: se o vídeo já estiver em ``to_status`` e não houver campos
        para atualizar, retorna ``changed=False`` sem gravar histórico duplicado.
        """
        row = self.db.get_video(video_id)
        if row is None:
            raise PipelineError(f"Vídeo não encontrado: {video_id}")

        from_status = row["status"]
        if from_status == to_status and not fields:
            return TransitionResult(video_id, from_status, to_status, changed=False)

        if from_status != to_status and not self.can_transition(from_status, to_status):
            raise PipelineError(
                f"Transição não permitida para vídeo {video_id}: "
                f"{from_status} -> {to_status}"
            )

        self.db.transition(
            video_id, to_status, actor=actor, note=note, **fields
        )
        return TransitionResult(video_id, from_status, to_status, changed=True)

    def fail(
        self,
        video_id: str,
        error_message: str,
        *,
        actor: str = "system",
        to_status: str = "FAILED",
    ) -> None:
        """Registra falha, incrementa tentativas e impede avanço indevido."""
        row = self.db.get_video(video_id)
        if row is None:
            raise PipelineError(f"Vídeo não encontrado: {video_id}")
        if row["status"] in {"PUBLISHED", "FAILED"} and to_status == "FAILED":
            # Já terminal; apenas atualiza mensagem de erro sem histórico extra.
            self.db.record_error(video_id, error_message, to_status=to_status, actor=actor)
            return
        self.db.record_error(video_id, error_message, to_status=to_status, actor=actor)

    def is_terminal(self, status: str) -> bool:
        self.validate_state(status)
        # StateMachine.is_terminal() connects PUBLISHED/FAILED → StateMachine
        # Graph edge: StateMachine node ↔ PUBLISHED, FAILED terminal states
        return status in {"PUBLISHED", "FAILED"}

    def is_publishable(self, status: str) -> bool:
        # StateMachine.is_publishable() connects APPROVED → publishable state
        # Graph edge: APPROVED state → Cloudinary/Buffer publication pipeline
        """Só pode subir para Cloudinary/Buffer a partir de APPROVED."""
        return status == "APPROVED"

    def is_approvable(self, status: str) -> bool:
        return status == "AWAITING_APPROVAL"

    def require_approval_state(self, video_id: str) -> None:
        row = self.db.get_video(video_id)
        if row is None:
            raise PipelineError(f"Vídeo não encontrado: {video_id}")
        if not self.is_approvable(row["status"]):
            raise PipelineError(
                f"Vídeo {video_id} não está em AWAITING_APPROVAL (atual: {row['status']})"
            )


__all__ = ["StateMachine", "TransitionResult"]
