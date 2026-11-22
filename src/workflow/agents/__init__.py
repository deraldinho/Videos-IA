"""Agentes especializados do workflow LangGraph (CP05–CP10).

Expõe cada agente como um nó compatível com ``StateGraph(dict)``. Todos herdam
de :class:`src.workflow.agents.base.BaseAgent` e devolvem o estado completo
(mutado) para que o grafo não perca dados entre nós (``StateGraph`` substitui,
não mescla, estados parciais).
"""

from .base import BaseAgent
from .compliance import ComplianceAgent
from .factcheck import FactCheckAgent
from .idea import IdeaAgent
from .monitoring import MonitoringAgent
from .publishing import PublishingAgent
from .render import RenderAgent
from .research import ResearchAgent
from .transcription import TranscriptionAgent
from .visual import VisualAgent
from .voice import VoiceAgent

__all__ = [
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
