"""Agente de verificação de fatos: valida as afirmações do roteiro."""

from __future__ import annotations

from typing import Any

from src.workflow.agents.base import BaseAgent
from src.workflow.state import Stage


class FactCheckAgent(BaseAgent):
    """Verifica fatos e fontes do roteiro antes da geração final.

    Responsabilidades (CP06):
    - Consome ``script`` e ``idea`` do estado.
    - Produz ``factcheck`` com veredito (ok/pendente/rejeitado) e notas.
    - Não faz checagem real externa: simula verificação determinística.
    - Avança current_stage para FACTCHECKED.
    """

    def __init__(self) -> None:
        super().__init__("FactCheckAgent")

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        state = self._ensure_state(state)
        script = state.get("script") or {}
        title = script.get("title", "") if isinstance(script, dict) else ""

        # Verificação simulada: todas as ideias passam em modo offline.
        factcheck = {
            "verdict": "ok",
            "checked_claims": len(script.get("scenes", [])) if isinstance(script, dict) else 0,
            "sources_verified": len(state.get("research_sources") or []),
            "notes": "Todas as afirmações verificadas (mock).",
            "title": title,
        }

        state["factcheck"] = factcheck
        state["current_stage"] = "FACTCHECKED"
        state["status"] = "FACTCHECKED"
        state["_next"] = "supervisor"
        state["_step"] = "factcheck_agent"
        self._add_cost(state, self.name, "verificacao_fatos", tokens=30, cost_usd=0.0)
        return self._mark(state, self.name, "factcheck",
                          f"Veredito: {factcheck['verdict']} ({factcheck['checked_claims']} afirmações)",
                          title=title, verdict=factcheck["verdict"])
