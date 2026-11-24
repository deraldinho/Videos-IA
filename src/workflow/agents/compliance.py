"""Agente de conformidade: verifica modos DRY_RUN/REVIEW/AUTO e regras de publicação."""

from __future__ import annotations

from typing import Any

from src.publication_policy import can_publish
from src.workflow.agents.base import BaseAgent
from src.workflow.state import ApprovalStatus, PublicationStatus, Stage


class ComplianceAgent(BaseAgent):
    """Aplica regras de conformidade antes da publicação.

    Responsabilidades (CP09):
    - Delegar a decisão de autorização para ``can_publish`` (ponto único de
      verdade, CR-05), evitando regras duplicadas entre agentes.
    - Impede publicação real em REVIEW/DRY_RUN sem aprovação explícita.
    - Avança current_stage para COMPLIANCE_CHECKED.
    - Nunca altera credenciais nem faz upload real sem autorização.
    """

    def __init__(self) -> None:
        super().__init__("ComplianceAgent")

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        state = self._ensure_state(state)
        mode = str(state.get("mode", "REVIEW")).upper()
        live_enabled = bool(state.get("live_enabled", False))
        auto_enabled = bool(state.get("auto_enabled", False))

        # --- Ponto único de verdade: can_publish (CR-05) ---------------------
        decision = can_publish(
            state={
                "mode": mode,
                "live_enabled": live_enabled,
                "auto_enabled": auto_enabled,
                "approval_status": state.get("approval_status", ApprovalStatus.PENDING.value),
            }
        )

        checks = list(decision.checks)
        blocked = not decision.authorized

        # Regra 3 complementar: sem aprovação, não avança além de AWAITING_APPROVAL.
        approval = state.get("approval_status", ApprovalStatus.PENDING.value)
        if approval == ApprovalStatus.REJECTED.value:
            checks.append("Roteiro rejeitado: requer correção antes de publicar")
            blocked = True

        state["compliance"] = {
            "mode": mode,
            "live_enabled": live_enabled,
            "auto_enabled": auto_enabled,
            "blocked": blocked,
            "checks": checks,
            "publication_authorized": decision.authorized,
            "publication_reason": decision.reason,
        }
        state["current_stage"] = "COMPLIANCE_CHECKED"
        state["status"] = "COMPLIANCE_CHECKED"
        state["_next"] = "supervisor"
        state["_step"] = "compliance_agent"
        self._add_cost(state, self.name, "compliance", tokens=10, cost_usd=0.0)
        return self._mark(state, self.name, "compliance",
                          f"Conformidade: {'bloqueado' if blocked else 'ok'} (modo={mode})",
                          mode=mode, blocked=blocked, checks=checks)
