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
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.contracts import VIDEO_STATES, Stage


WORKFLOW_VERSION = "1.0.0"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Enums                                                                       #
# --------------------------------------------------------------------------- #
# Stage é importado de src.contracts (fonte única de verdade).
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
    # CP22 — Research Intelligence + FactCheck real
    research_result: Optional[ResearchResult] = None
    factcheck_result: Optional[FactCheckResult] = None
    duplicate_topics: list[DuplicateTopic] = Field(default_factory=list)
    # CP23 — Scene Asset Intelligence
    visual_prompts: list[VisualPrompt] = Field(default_factory=list)
    scene_assets: list[SceneAsset] = Field(default_factory=list)
    character_identities: list[CharacterIdentity] = Field(default_factory=list)
    asset_cache: list[AssetCacheEntry] = Field(default_factory=list)
    # CP24 — Publicação multicanal
    publication_targets: list[PublicationTarget] = Field(default_factory=list)
    # CP25 — Analytics Intelligence
    metric_snapshots: list[MetricSnapshot] = Field(default_factory=list)
    experiments: list[Experiment] = Field(default_factory=list)
    channel_profiles: dict[str, ChannelProfile] = Field(default_factory=dict)
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


# --------------------------------------------------------------------------- #
# CP22 — Research Intelligence + FactCheck real                              #
# --------------------------------------------------------------------------- #
class ResearchSource(BaseModel):
    """Fonte de pesquisa verificada com evidência editorial e proveniência."""

    model_config = ConfigDict(strict=True)

    source_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    title: str = Field(default="")
    url: str = Field(default="")
    publisher: str = Field(default="")
    accessed_at: str = Field(default_factory=_utc_now_iso)
    editorial_evidence: str = Field(default="")
    fetched_at: str = Field(default_factory=_utc_now_iso)
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    reliability_score: float = Field(default=0.0, ge=0.0, le=1.0)
    content_hash: Optional[str] = None


class ResearchResult(BaseModel):
    """Resultado da pesquisa sobre um tópico/nicho."""

    model_config = ConfigDict(strict=True)

    topic: str = Field(default="")
    sources: list[ResearchSource] = Field(default_factory=list)
    total_sources: int = Field(default=0, ge=0)
    research_method: str = Field(default="mock")
    evidence_level: str = Field(default="none")  # none | partial | verified
    generated_at: str = Field(default_factory=_utc_now_iso)


class ClaimStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    REFUTED = "refuted"


class FactCheckVerdict(str, Enum):
    OK = "ok"
    PENDING = "pending"
    REJECTED = "rejected"


class FactCheckClaim(BaseModel):
    """Uma afirmação do roteiro submetida a fact-check."""

    model_config = ConfigDict(strict=True)

    claim_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    claim_text: str = Field(default="")
    scene_role: str = Field(default="")
    scene_index: int = Field(default=0, ge=0)
    source_ids: list[str] = Field(default_factory=list)
    status: ClaimStatus = ClaimStatus.UNVERIFIED
    evidence_urls: list[str] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    checked_at: Optional[str] = None


class FactCheckResult(BaseModel):
    """Resultado da verificação de fatos."""

    model_config = ConfigDict(strict=True)

    verdict: FactCheckVerdict = FactCheckVerdict.PENDING
    checked_claims: int = Field(default=0, ge=0)
    verified_claims: int = Field(default=0, ge=0)
    rejected_claims: int = Field(default=0, ge=0)
    pending_claims: int = Field(default=0, ge=0)
    sources_verified: int = Field(default=0, ge=0)
    notes: str = Field(default="")
    blocking: bool = Field(default=False)
    claims: list[FactCheckClaim] = Field(default_factory=list)
    checked_at: str = Field(default_factory=_utc_now_iso)


class DuplicateTopic(BaseModel):
    """Registro de tópico já pesquisado/usado (detecção de repetição)."""

    model_config = ConfigDict(strict=True)

    topic_hash: str = Field(default="")
    topic: str = Field(default="")
    first_seen: str = Field(default_factory=_utc_now_iso)
    occurrence_count: int = Field(default=1, ge=1)


# --------------------------------------------------------------------------- #
# CP24 — Publicação multicanal                                               #
# --------------------------------------------------------------------------- #
class PublicationTarget(BaseModel):
    """Destino de publicação com estado local e remoto, idempotência e recibos.

    Invariante-chave: confirmação inequívoca de plataforma antes de PUBLISHED.
    local_state (none/uploaded/queued/publishing/published/failed) é o estado
    local do pipeline; remote_status vem da confirmação remota real.
    """

    model_config = ConfigDict(strict=True)

    target_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    video_id: str = Field(default="")
    platform: str = Field(default="")  # tiktok, youtube_shorts, instagram_reels
    channel_id: str = Field(default="")
    idempotency_key: str = Field(default="")  # f"{video_id}:{platform}"
    local_state: str = Field(default="none")  # none, uploaded, queued, publishing, published, failed
    remote_id: Optional[str] = None
    remote_status: Optional[str] = None
    confirmed_at: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = Field(default=0, ge=0)
    # Detalhes remotos por plataforma (preenchidos pelos adaptadores)
    cloudinary_url: Optional[str] = None
    cloudinary_id: Optional[str] = None
    buffer_post_id: Optional[str] = None
    buffer_post_state: Optional[str] = None
    # Confirmação remota inequívoca de publicação (CP28)
    publication_confirmed_at: Optional[str] = None
    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)

    @model_validator(mode="after")
    def _set_idempotency_key(self) -> "PublicationTarget":
        """Gera idempotency_key a partir de video_id:platform quando ausente."""
        if not self.idempotency_key and self.video_id and self.platform:
            self.idempotency_key = f"{self.video_id}:{self.platform}"
        return self


# --------------------------------------------------------------------------- #
# CP25 — Analytics Intelligence                                             #
# --------------------------------------------------------------------------- #
class MetricSnapshot(BaseModel):
    """Instantâneo imutável de métrica de uma publicação.

    provenance (mock|real|estimated|unavailable) nunca se mistura com outros
    valores; ausência de dado não equivale a zero.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    snapshot_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    target_id: str = Field(default="")
    platform: str = Field(default="")
    publication_id: str = Field(default="")
    observed_at: str = Field(default_factory=_utc_now_iso)
    source: str = Field(default="")  # nome do provedor de analytics
    provenance: str = Field(default="mock")  # mock | real | estimated | unavailable
    publication_confirmed: bool = Field(default=False)  # vinculada a publicação confirmada remotamente
    metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utc_now_iso)


class Experiment(BaseModel):
    """Experimento de teste A/B de métricas de publicação."""

    model_config = ConfigDict(strict=True)

    experiment_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    hypothesis: str = Field(default="")
    variants: list[str] = Field(default_factory=list)
    measurement_window: str = Field(default="")
    metric_definition: dict[str, Any] = Field(default_factory=dict)
    decision_status: str = Field(default="inconclusive")  # inconclusive, confirmed, rejected
    created_at: str = Field(default_factory=_utc_now_iso)


class ChannelProfile(BaseModel):
    """Perfil de canal: plataforma, nicho, política de publicação."""

    model_config = ConfigDict(strict=True)

    channel_id: str = Field(default="")
    platform: str = Field(default="")
    niche: str = Field(default="")
    language: str = Field(default="pt-BR")
    visual_preset: str = Field(default="vertical_1080x1920")
    budget_daily_usd: float = Field(default=0.0, ge=0)
    publication_policy: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utc_now_iso)


# --------------------------------------------------------------------------- #
# CP23 — Scene Asset Intelligence                                           #
# --------------------------------------------------------------------------- #
class AssetProvenance(BaseModel):
    """Proveniência de um asset visual: origem, licença, modelo e custo."""

    model_config = ConfigDict(strict=True)

    asset_id: str = Field(default="")
    provider: str = Field(default="")
    model: str = Field(default="")
    prompt: str = Field(default="")
    license_type: str = Field(default="unknown")
    license_url: Optional[str] = None
    cost_usd: float = Field(default=0.0, ge=0.0)
    generated_at: str = Field(default_factory=_utc_now_iso)
    verified: bool = Field(default=False)


class CharacterIdentity(BaseModel):
    """Identidade visual consistente de um personagem entre cenas."""

    model_config = ConfigDict(strict=True)

    character_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    name: str = Field(default="")
    description: str = Field(default="")
    visual_traits: list[str] = Field(default_factory=list)
    consistency_hash: str = Field(default="")
    reference_asset_id: Optional[str] = None


class SceneAsset(BaseModel):
    """Recurso visual associado a uma cena do roteiro."""

    model_config = ConfigDict(strict=True)

    asset_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    scene_id: str = Field(default="")
    scene_role: str = Field(default="")
    scene_index: int = Field(default=0, ge=0)
    prompt: str = Field(default="")
    model: str = Field(default="")
    url: str = Field(default="")
    file_hash: str = Field(default="")
    content_hash: str = Field(default="")
    width: Optional[int] = Field(default=None, ge=0)
    height: Optional[int] = Field(default=None, ge=0)
    format: str = Field(default="png")
    size_bytes: int = Field(default=0, ge=0)
    provenance: AssetProvenance = Field(default_factory=AssetProvenance)
    character_id: Optional[str] = None
    reused_from: Optional[str] = None  # asset_id de um asset reaproveitado
    created_at: str = Field(default_factory=_utc_now_iso)


class VisualPrompt(BaseModel):
    """Prompt visual derivado do roteiro para uma cena."""

    model_config = ConfigDict(strict=True)

    prompt_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    scene_id: str = Field(default="")
    scene_role: str = Field(default="")
    scene_index: int = Field(default=0, ge=0)
    prompt_text: str = Field(default="")
    style: str = Field(default="vertical_1080x1920")
    model: str = Field(default="")
    character_ids: list[str] = Field(default_factory=list)
    negative_prompt: str = Field(default="")
    created_at: str = Field(default_factory=_utc_now_iso)


class AssetCacheEntry(BaseModel):
    """Entrada de cache de imagem por hash de conteúdo."""

    model_config = ConfigDict(strict=True)

    content_hash: str = Field(default="")
    asset_id: str = Field(default="")
    url: str = Field(default="")
    hit_count: int = Field(default=0, ge=0)
    created_at: str = Field(default_factory=_utc_now_iso)
    last_accessed: str = Field(default_factory=_utc_now_iso)


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
    # CP22
    "ResearchSource",
    "ResearchResult",
    "ClaimStatus",
    "FactCheckVerdict",
    "FactCheckClaim",
    "FactCheckResult",
    "DuplicateTopic",
    # CP23
    "AssetProvenance",
    "CharacterIdentity",
    "SceneAsset",
    "VisualPrompt",
    "AssetCacheEntry",
    # CP24
    "PublicationTarget",
    # CP25
    "MetricSnapshot",
    "Experiment",
    "ChannelProfile",
    "empty_state",
    "state_to_dict",
    "dict_to_state",
]
