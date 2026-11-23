"""Agente de verificação de fatos: valida as afirmações do roteiro (CP22).

Evolução do agente original (CP06):

- Usa ``FactCheckEngine`` para verificar afirmações contra fontes pesquisadas.
- Pontuação de confiabilidade e detecção de afirmações sem evidências.
- Bloqueio de conteúdo factual não verificado (veredito REJECTED/PENDING com
  blocking=True) — nunca aprova fato sem evidência real.
- Modo offline padrão: fontes mock não constituem evidência; veredito fica
  PENDING com bloqueio quando há afirmações não verificadas.
- Avança current_stage para FACTCHECKED.
"""

from __future__ import annotations

from typing import Any

from src.workflow.agents.base import BaseAgent
from src.workflow.research_connectors import (
    MockResearchConnector,
    FactCheckEngine,
)
from src.workflow.state import Stage


class FactCheckAgent(BaseAgent):
    """Verifica fatos e fontes do roteiro antes da geração final.

    Responsabilidades (CP22):
    - Consome ``script`` e ``research_result`` do estado.
    - Verifica afirmações contra fontes externas (ou mock em modo offline).
    - Pontuação de relevância, originalidade e confiabilidade.
    - Detecção de afirmações sem evidências.
    - Bloqueio de conteúdo factual não verificado.
    - Nunca apresenta fontes simuladas como reais.
    - Avança current_stage para FACTCHECKED.
    """

    def __init__(self, connector=None) -> None:
        super().__init__("FactCheckAgent")
        self.connector = connector or MockResearchConnector()
        self.engine = FactCheckEngine(self.connector)

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        state = self._ensure_state(state)
        script = state.get("script") or {}
        research_dump = state.get("research_result") or {}

        # Reconstrói o ResearchResult a partir do estado persistido.
        from src.workflow.state import ResearchResult  # import local para evitar ciclo

        research = ResearchResult.model_validate(research_dump) if research_dump else ResearchResult()

        factcheck = self.engine.run(script, research)

        state["factcheck"] = factcheck.model_dump(mode="json")
        state["factcheck_result"] = factcheck.model_dump(mode="json")

        # Quando há bloqueio (conteúdo factual não verificado), não avança para
        # o roteiro: o supervisor deve parar ou exigir revisão humana.
        if factcheck.blocking:
            state["current_stage"] = "FACTCHECKED"
            state["status"] = "FACTCHECKED"
            state["_next"] = "supervisor"
            state["_step"] = "factcheck_agent"
            self._add_cost(
                state,
                self.name,
                "verificacao_fatos",
                tokens=30,
                cost_usd=0.0,
            )
            return self._mark(
                state,
                self.name,
                "factcheck",
                f"BLOQUEADO: {factcheck.notes}",
                verdict=factcheck.verdict.value,
                blocking=True,
                checked_claims=factcheck.checked_claims,
            )

        state["current_stage"] = "FACTCHECKED"
        state["status"] = "FACTCHECKED"
        state["_next"] = "supervisor"
        state["_step"] = "factcheck_agent"
        self._add_cost(
            state,
            self.name,
            "verificacao_fatos",
            tokens=30,
            cost_usd=0.0,
        )
        return self._mark(
            state,
            self.name,
            "factcheck",
            f"Veredito: {factcheck.verdict.value} "
            f"({factcheck.verified_claims}/{factcheck.checked_claims} verificadas)",
            verdict=factcheck.verdict.value,
            checked_claims=factcheck.checked_claims,
        )


__all__ = ["FactCheckAgent"]
