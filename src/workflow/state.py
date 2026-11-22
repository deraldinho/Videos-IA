"""Contratos tipados para o estado compartilhado do workflow LangGraph.

Define os modelos Pydantic que descrevem o estado de execução de um vídeo
durante todo o ciclo (pesquisa → roteiro → narração → visual → render →
validação → aprovação → publicação → monitoramento → auditoria).

Os modelos servem de contrato entre os agentes especializados e podem ser
serializados para/deserializados de dicionário simples (compatível com o
``SqliteSaver`` do LangGraph, que armazena estado como JSON).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


WORKFLOW_VERSION = "1.0.0"

VIDEO_STATES = (
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
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Enums                                                                       #
# --------------------------------------------------------------------------- #
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


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PublicationStatus(str, Enum):
    NONE = "none"
    QUEUED = "queued"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


# --------------------------------------------------------------------------- #
# Sub-modelos                                                                 #
# --------------------------------------------------------------------------- #
class ScriptScene(BaseModel):
    """Uma cena do roteiro."""

    model_config = ConfigDict(strict=True)

    role: str = Field(default="corpo", description="p. gancho, corpo, cta")
    seconds: float = Field(default=0.0, ge=0)
    heading: str = Field(default="")
    narration: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class Script(BaseModel):
    """Roteiro completo de um vídeo."""

    model_config = ConfigDict(strict=True)

    topic: str = Field(default="")
    title: str = Field(default="")
    description: str = Field(default="")
    hashtags: list[str] = Field(default_factory=list)
    scenes: list[ScriptScene] = Field(default_factory=list)
    total_seconds: float = Field(default=0.0, ge=0)


class AudioAsset(BaseModel):
    """Arquivo de áudio gerado (narração TTS)."""

    model_config = ConfigDict(strict=True)

    scene_role: str = Field(default="")
    index: int = Field(default=0)
    text: str = Field(default="")
    wav_path: str = Field(default="")
    duration_seconds: float = Field(default=0.0, ge=0)


class VideoAsset(BaseModel):
    """Arquivo de vídeo renderizado (MP4)."""

    model_config = ConfigDict(strict=True)

    mp4_path: Optional[str] = None
    width: Optional[int] = Field(default=None, ge=0)
    height: Optional[int] = Field(default=None, ge=0)
    fps: Optional[float] = Field(default=None, ge=0)
    duration_seconds: Optional[float] = Field(default=None, ge=0)
    has_audio: bool = False
    size_bytes: int = Field(default=0, ge=0)


class ValidationResult(BaseModel):
    """Resultado de um quality gate audiovisual."""

    model_config = ConfigDict(strict=True)

    valid: bool = False
    width: Optional[int] = Field(default=None, ge=0)
    height: Optional[int] = Field(default=None, ge=0)
    fps: Optional[float] = Field(default=None, ge=0)
    duration_seconds: Optional[float] = Field(default=None, ge=0)
    has_audio: bool = False
    errors: list[str] = Field(default_factory=list)


class AuditEntry(BaseModel):
    """Uma entrada do histórico de auditoria de uma execução."""

    model_config = ConfigDict(strict=True)

    actor: str = Field(default="system")
    action: str = Field(default="")
    note: Optional[str] = None
    timestamp: str = Field(default_factory=_utc_now_iso)
    fields: dict[str, Any] = Field(default_factory=dict)


class ErrorEntry(BaseModel):
    """Registro de erro de um estágio."""

    model_config = ConfigDict(strict=True)

    stage: str = Field(default="")
    message: str = Field(default="")
    retry_count: int = Field(default=0, ge=0)
    timestamp: str = Field(default_factory=_utc_now_iso)


class CostEntry(BaseModel):
    """Custo / consumo de tokens de um agente."""

    model_config = ConfigDict(strict=True)

    agent: str = Field(default="")
    operation: str = Field(default="")
    tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)
    timestamp: str = Field(default_factory=_utc_now_iso)


# --------------------------------------------------------------------------- #
# Estado compartilhado                                                        #
# --------------------------------------------------------------------------- #
class WorkflowState(BaseModel):
    """Estado compartilhado de um vídeo através de todos os agentes.

    Um dicionário compatível com LangGraph pode ser obtido com
    :meth:`model_dump` e reconstruído com :meth:`model_validate`.
    """

    model_config = ConfigDict(strict=False)

    execution_id: str
    video_id: str
    workflow_version: str = WORKFLOW_VERSION
    status: str = Stage.IDEA_CREATED
    current_stage: str = Stage.IDEA_CREATED
    topic: Optional[str] = None
    kind: Optional[str] = None
    mode: str = "REVIEW"
    live_enabled: bool = False
    auto_enabled: bool = False
    tts_required: bool = False
    research_sources: list[dict[str, Any]] = Field(default_factory=list)
    idea: Optional[dict[str, Any]] = None
    factcheck: Optional[dict[str, Any]] = None
    script: Optional[Script] = None
    storyboard: Optional[dict[str, Any]] = None
    audio_assets: list[AudioAsset] = Field(default_factory=list)
    video_assets: list[VideoAsset] = Field(default_factory=list)
    transcription: Optional[dict[str, Any]] = None
    validation_results: Optional[ValidationResult] = None
    compliance: Optional[dict[str, Any]] = None
    monitoring: Optional[dict[str, Any]] = None
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    publication_status: PublicationStatus = PublicationStatus.NONE
    cloudinary_url: Optional[str] = None
    cloudinary_id: Optional[str] = None
    buffer_post_id: Optional[str] = None
    buffer_post_state: Optional[str] = None
    costs: list[CostEntry] = Field(default_factory=list)
    errors: list[ErrorEntry] = Field(default_factory=list)
    retries: int = Field(default=0, ge=0)
    audit_history: list[AuditEntry] = Field(default_factory=list)
    started_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)

    def mark_updated(self, actor: str = "system", action: str = "",
                     note: Optional[str] = None, **fields: Any) -> None:
        """Atualiza ``updated_at`` e grava uma entrada de auditoria."""
        self.updated_at = _utc_now_iso()
        self.audit_history.append(
            AuditEntry(actor=actor, action=action, note=note, fields=fields)
        )

    def add_error(self, stage: str, message: str) -> None:
        """Registra um erro e incrementa o contador de tentativas."""
        self.errors.append(ErrorEntry(stage=stage, message=message,
                                      retry_count=self.retries + 1))
        self.retries += 1
        self.mark_updated(actor="system", action="error",
                          note=f"{stage}: {message}")

    def add_cost(self, agent: str, operation: str, tokens: int = 0,
                 cost_usd: float = 0.0) -> None:
        """Registra consumo de tokens/custo de um agente."""
        self.costs.append(CostEntry(agent=agent, operation=operation,
                                    tokens=tokens, cost_usd=cost_usd))
        self.mark_updated(actor=agent, action="cost",
                          note=f"{operation}: {tokens} tokens")


def empty_state(execution_id: str, video_id: str,
                topic: Optional[str] = None) -> WorkflowState:
    """Cria um :class:`WorkflowState` inicial (IDEA_CREATED)."""
    now = _utc_now_iso()
    return WorkflowState(
        execution_id=execution_id,
        video_id=video_id,
        status=Stage.IDEA_CREATED,
        current_stage=Stage.IDEA_CREATED,
        topic=topic,
        mode="REVIEW",
        live_enabled=False,
        auto_enabled=False,
        started_at=now,
        updated_at=now,
    )


def state_to_dict(state: WorkflowState) -> dict[str, Any]:
    """Serializa o estado para dicionário simples (JSON-amigável)."""
    return state.model_dump(mode="json")


def dict_to_state(data: dict[str, Any]) -> WorkflowState:
    """Reconstrói um :class:`WorkflowState` a partir de um dicionário."""
    return WorkflowState.model_validate(data)


__all__ = [
    "WORKFLOW_VERSION",
    "VIDEO_STATES",
    "Stage",
    "ApprovalStatus",
    "PublicationStatus",
    "ScriptScene",
    "Script",
    "AudioAsset",
    "VideoAsset",
    "ValidationResult",
    "AuditEntry",
    "ErrorEntry",
    "CostEntry",
    "WorkflowState",
    "empty_state",
    "state_to_dict",
    "dict_to_state",
]
