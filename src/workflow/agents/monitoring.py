"""Agente de monitoramento: rastreia execução, métricas e logs do pipeline."""

from __future__ import annotations

from typing import Any

from src.workflow.agents.base import BaseAgent
from src.workflow.state import Stage


class MonitoringAgent(BaseAgent):
    """Monitora a execução do pipeline e agrega métricas.

    Responsabilidades (CP10):
    - Consome todo o estado (custos, erros, auditoria, publicação).
    - Produz ``monitoring`` com resumo de execução, métricas e alertas.
    - Não altera o fluxo: apenas observa e registra.
    - Avança current_stage para MONITORED.
    """

    def __init__(self) -> None:
        super().__init__("MonitoringAgent")

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        state = self._ensure_state(state)
        video_id = state["video_id"]
        status = state.get("status", "IDEA_CREATED")
        costs = state.get("costs") or []
        errors = state.get("errors") or []
        audit = state.get("audit_history") or []

        total_tokens = sum(c.get("tokens", 0) for c in costs)
        total_cost = sum(c.get("cost_usd", 0.0) for c in costs)
        error_count = len(errors)
        last_error = errors[-1] if errors else None

        monitoring = {
            "video_id": video_id,
            "status": status,
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "error_count": error_count,
            "last_error": last_error,
            "audit_entries": len(audit),
            "publication_status": state.get("publication_status", "none"),
            "cloudinary_url": state.get("cloudinary_url"),
            "buffer_post_id": state.get("buffer_post_id"),
            "retries": state.get("retries", 0),
        }

        state["monitoring"] = monitoring
        state["current_stage"] = "MONITORED"
        state["status"] = "MONITORED"
        state["_next"] = "supervisor"
        state["_step"] = "monitoring_agent"
        self._add_cost(state, self.name, "monitoramento", tokens=5, cost_usd=0.0)
        return self._mark(state, self.name, "monitor",
                          f"Status={status}, erros={error_count}, custo=${total_cost:.6f}",
                          video_id=video_id, metrics=monitoring)
