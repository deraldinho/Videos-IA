"""Agente de pesquisa: coleta fontes e dados para o tema do vídeo (CP22).

Evolução do agente original (CP05):

- Usa ``ResearchIntelligence`` com um ``ResearchConnector`` autorizado.
- Modo offline padrão: ``MockResearchConnector`` produz fontes
  explicitamente não-reais (evidence_level "none"), NUNCA apresentadas como
  pesquisa real.
- Armazena URLs, datas, autores e evidências em ``ResearchSource``.
- Pontua relevância, originalidade e confiabilidade de cada fonte.
- Detecta tópicos repetidos (DuplicateTopic).
- Nunca apresenta fontes simuladas como reais.
- Avança o status para IDEA_RESEARCHED.
"""

from __future__ import annotations

from typing import Any

from src.workflow.agents.base import BaseAgent
from src.workflow.research_connectors import (
    MockResearchConnector,
    ResearchIntelligence,
)
from src.workflow.state import Stage


class ResearchAgent(BaseAgent):
    """Pesquisa fontes e dados sobre o tópico antes da geração do roteiro.

    Responsabilidades (CP22):
    - Recebe o ``topic`` do estado.
    - Conectores autorizados para fontes verificáveis.
    - Armazenamento de URLs, datas, autores e evidências.
    - Pontuação de relevância, originalidade e confiabilidade.
    - Identificação de assuntos repetidos.
    - Nunca apresenta fontes simuladas como reais.
    - Avança current_stage para IDEA_RESEARCHED.
    """

    def __init__(self, connector=None) -> None:
        super().__init__("ResearchAgent")
        self.connector = connector or MockResearchConnector()
        self.intelligence = ResearchIntelligence(self.connector)

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        state = self._ensure_state(state)
        topic = state.get("topic") or "a definir"

        # Tópicos já pesquisados (detecção de repetição).
        previous = state.get("duplicate_topics") or []
        previous_topics = []
        for p in previous:
            if isinstance(p, dict):
                previous_topics.append(p)

        result = self.intelligence.research(topic, previous_topics)

        # Registra tópico repetido, se aplicável.
        topic_hash = self.intelligence._topic_hash(topic)
        already = [p for p in previous_topics if p.get("topic_hash") == topic_hash]
        if already:
            already[0]["occurrence_count"] = int(already[0].get("occurrence_count", 1)) + 1
        else:
            previous_topics.append(
                self.intelligence.build_duplicate_topic(topic).model_dump(mode="json")
            )
        state["duplicate_topics"] = previous_topics

        # Fontes (sempre como dicts, compatível com o estado legado).
        sources_dump = [s.model_dump(mode="json") for s in result.sources]
        state["research_sources"] = sources_dump

        # Resultado tipado de pesquisa.
        state["research_result"] = result.model_dump(mode="json")

        state["current_stage"] = "IDEA_RESEARCHED"
        state["status"] = "IDEA_RESEARCHED"
        state["_next"] = "supervisor"
        state["_step"] = "research_agent"
        self._add_cost(
            state,
            self.name,
            "pesquisa",
            tokens=len(topic) * 2,
            cost_usd=0.0,
        )
        return self._mark(
            state,
            self.name,
            "research",
            f"{len(sources_dump)} fontes para '{topic}' "
            f"(método={result.research_method}, evidência={result.evidence_level})",
            topic=topic,
            source_count=len(sources_dump),
            evidence_level=result.evidence_level,
        )


__all__ = ["ResearchAgent"]
