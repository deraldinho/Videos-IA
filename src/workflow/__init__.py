"""Pacote de fundação do workflow LangGraph (CP03–CP10).

Contratos tipados com Pydantic para o estado compartilhado entre os agentes
especializados, serializáveis para JSON (compatível com SqliteSaver), e os
próprios agentes especializados.
"""

from .agents import (
    BaseAgent,
    ComplianceAgent,
    FactCheckAgent,
    IdeaAgent,
    MonitoringAgent,
    PublishingAgent,
    RenderAgent,
    ResearchAgent,
    TranscriptionAgent,
    VisualAgent,
    VoiceAgent,
)
from .state import (
    WORKFLOW_VERSION,
    VIDEO_STATES,
    Stage,
    ApprovalStatus,
    PublicationStatus,
    ScriptScene,
    Script,
    AudioAsset,
    VideoAsset,
    ValidationResult,
    AuditEntry,
    ErrorEntry,
    CostEntry,
    WorkflowState,
    empty_state,
    state_to_dict,
    dict_to_state,
)

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
    "BaseAgent",
    "ComplianceAgent",
    "FactCheckAgent",
    "IdeaAgent",
    "MonitoringAgent",
    "PublishingAgent",
    "RenderAgent",
    "ResearchAgent",
    "TranscriptionAgent",
    "VisualAgent",
    "VoiceAgent",
]
