"""Agente de ideias: gera ideias originais de roteiro a partir da pesquisa."""

from __future__ import annotations

from typing import Any

from src.workflow.agents.base import BaseAgent
from src.workflow.state import Stage


class IdeaAgent(BaseAgent):
    """Gera uma ideia original de roteiro a partir das fontes pesquisadas.

    Responsabilidades (CP05):
    - Consome ``research_sources`` e ``topic`` do estado.
    - Define ``idea`` (título, ângulo, gancho, CTA) no estado.
    - Não chama LLM real: produz ideia determinística baseada nas fontes.
    - Avança current_stage para IDEA_READY.
    """

    def __init__(self) -> None:
        super().__init__("IdeaAgent")

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        state = self._ensure_state(state)
        topic = state.get("topic") or "a definir"
        sources = state.get("research_sources") or []

        idea = {
            "topic": topic,
            "title": f"{topic}: o que você precisa saber",
            "angle": "explicação rápida e direta",
            "hook": f"Já parou para pensar em {topic}?",
            "cta": "Curte, segue e compartilha.",
            "sources_used": [s.get("title") for s in sources],
        }

        state["idea"] = idea
        state["current_stage"] = "IDEA_READY"
        state["status"] = "IDEA_READY"
        state["_next"] = "supervisor"
        state["_step"] = "idea_agent"
        self._add_cost(state, self.name, "geracao_ideia", tokens=50, cost_usd=0.001)
        return self._mark(state, self.name, "idea",
                          f"Idea gerada: {idea['title']}", topic=topic)
