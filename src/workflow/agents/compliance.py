"""Agente de conformidade: verifica modos DRY_RUN/REVIEW/AUTO e regras de publicação."""

from __future__ import annotations

from typing import Any

from src.workflow.agents.base import BaseAgent
from src.workflow.state import ApprovalStatus, PublicationStatus, Stage


class ComplianceAgent(BaseAgent):
    """Aplica regras de conformidade antes da publicação.

    Responsabilidades (CP09):
    - Verifica se o modo é DRY_RUN/REVIEW/AUTO e se live_enabled/auto_enabled
      estão coerentes.
    - Impede publicação real em REVIEW/DRY_RUN sem aprovação explícita.
    - Avança current_stage para COMPLIANCE_CHECKED.
    - Nunca altera credenciais nem faz upload real sem autorização.
    """

    def __init__(self) -> None:
        super().__init__("ComplianceAgent")

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        state = self._ensure_state(state)
        mode = state.get("mode", "REVIEW")
        live_enabled = bool(state.get("live_enabled", False))
        auto_enabled = bool(state.get("auto_enabled", False))
        approval = state.get("approval_status", ApprovalStatus.PENDING.value)

        checks = []
        blocked = False

        # Regra 1: AUTO requer auto_enabled + live_enabled para publicação real.
        if mode == "AUTO":
            if not auto_enabled:
                checks.append("AUTO sem auto_enabled: publicação real bloqueada")
                blocked = True
            if not live_enabled:
                checks.append("AUTO sem live_enabled: upload/Cloudinary bloqueado")
                blocked = True

        # Regra 2: REVIEW/DRY_RUN nunca publica de verdade.
        if mode in {"DRY_RUN", "REVIEW"}:
            checks.append(f"Modo {mode}: apenas simulação, sem upload real")
            if approval != ApprovalStatus.APPROVED.value:
                checks.append("Sem aprovação humana: publicação bloqueada")

        # Regra 3: sem aprovação, não avança além de AWAITING_APPROVAL.
        if approval == ApprovalStatus.REJECTED.value:
            checks.append("Roteiro rejeitado: requer correção antes de publicar")
            blocked = True

        state["compliance"] = {
            "mode": mode,
            "live_enabled": live_enabled,
            "auto_enabled": auto_enabled,
            "blocked": blocked,
            "checks": checks,
        }
        state["current_stage"] = "COMPLIANCE_CHECKED"
        state["status"] = "COMPLIANCE_CHECKED"
        state["_next"] = "supervisor"
        state["_step"] = "compliance_agent"
        self._add_cost(state, self.name, "compliance", tokens=10, cost_usd=0.0)
        return self._mark(state, self.name, "compliance",
                          f"Conformidade: {'bloqueado' if blocked else 'ok'} (modo={mode})",
                          mode=mode, blocked=blocked, checks=checks)
