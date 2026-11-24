"""Agente de conformidade: verifica modos DRY_RUN/REVIEW/AUTO e regras de publicação."""

from __future__ import annotations

from typing import Any

from src.publication_policy import can_publish
from src.workflow.agents.base import BaseAgent
from src.workflow.state import ApprovalStatus, PublicationStatus, Stage


class ComplianceAgent(BaseAgent):
    """Aplica regras de conformidade antes da publicação.

    Responsabilidades (CP09):
    - Verifica coerência do modo (DRY_RUN/REVIEW/AUTO) com live_enabled/auto_enabled
      e registra a decisão de ``can_publish`` como informativo (ponto único de
      verdade, CR-05). Não bloqueia por falta de aprovação aqui: a aprovação
      humana ocorre no nó human_approval (após este agente), e o gate de
      publicação real (fail-closed) é aplicado em PublishingAgent antes do
      upload — evitando dependência circular conformidade <-> aprovação.
    - Impede avanço quando o roteiro foi rejeitado (approval == REJECTED).
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
        approval = state.get("approval_status", ApprovalStatus.PENDING.value)

        # --- Ponto único de verdade: can_publish (CR-05) ---------------------
        # Chamado aqui apenas para registrar o estado de autorização no
        # compliance (informativo/auditoria). O gate efetivo (fail-closed)
        # é aplicado em PublishingAgent antes do upload real.
        decision = can_publish(
            state={
                "mode": mode,
                "live_enabled": live_enabled,
                "auto_enabled": auto_enabled,
                "approval_status": approval,
            }
        )

        checks = list(decision.checks)
        blocked = False

        # Regra: roteiro rejeitado requer correção antes de publicar.
        if approval == ApprovalStatus.REJECTED.value:
            checks.append("Roteiro rejeitado: requer correção antes de publicar")
            blocked = True

        # Regra: modo AUTO requer auto_enabled=True E live_enabled=True para
        # publicacao real. Quando o unico impedimento e a falta de aprovacao
        # humana, nao bloqueia aqui: a aprovacao ocorre no no human_approval
        # (apos este agente), evitando a dependencia circular
        # conformidade <-> aprovacao (CR-05). O gate efetivo (fail-closed) e
        # aplicado no PublishingAgent antes do upload real.
        if mode == "AUTO" and (not auto_enabled or not live_enabled):
            checks.append(
                "Publicacao real bloqueada em modo AUTO sem auto_enabled e/ou live_enabled"
            )
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
