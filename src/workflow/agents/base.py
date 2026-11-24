"""Classe base para todos os agentes especializados do workflow LangGraph."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Any

from src.contracts import MAX_RETRIES as CONTRACTS_MAX_RETRIES
from src.workflow.state import _utc_now_iso


class BaseAgent(ABC):
    """Classe base para agentes especializados do pipeline LangGraph.

    Cada agente:
    - recebe um dict de estado (compatível com LangGraph / SqliteSaver)
    - garante campos básicos (_ensure_state)
    - executa sua responsabilidade em ``run()``
    - registra custo, erro e auditoria via helpers (_mark, _add_error, _add_cost)
    - devolve o estado completo (mutado) para o grafo
    """

    def __init__(self, name: str, *, max_retries: int = CONTRACTS_MAX_RETRIES) -> None:
        self.name = name
        self.max_retries = max_retries

    def _ensure_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """Garante que o estado dicionário tenha os campos básicos preenchidos."""
        if not state.get("execution_id"):
            state["execution_id"] = f"exec-{uuid.uuid4().hex[:12]}"
        if not state.get("video_id"):
            state["video_id"] = state["execution_id"]
        if not state.get("workflow_version"):
            state["workflow_version"] = "1.0.0"
        if not state.get("status"):
            state["status"] = "IDEA_CREATED"
        if not state.get("current_stage"):
            state["current_stage"] = "IDEA_CREATED"
        if state.get("started_at") is None:
            state["started_at"] = _utc_now_iso()
        if state.get("updated_at") is None:
            state["updated_at"] = _utc_now_iso()
        return state

    @abstractmethod
    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Executa a responsabilidade do agente sobre o estado."""

    def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        """Ponto de entrada compatível com nó do LangGraph."""
        return self.run(state)

    def _mark(self, state: dict[str, Any], actor: str, action: str,
              note: str | None = None, **fields: Any) -> dict[str, Any]:
        """Atualiza updated_at e grava uma entrada de auditoria."""
        state["updated_at"] = _utc_now_iso()
        history = list(state.get("audit_history", []) or [])
        history.append(
            {
                "actor": actor,
                "action": action,
                "note": note,
                "timestamp": state["updated_at"],
                "fields": fields,
            }
        )
        state["audit_history"] = history
        return state

    def _add_error(self, state: dict[str, Any], stage: str, message: str) -> dict[str, Any]:
        """Registra um erro e incrementa o contador de tentativas."""
        errors = list(state.get("errors", []) or [])
        errors.append(
            {
                "stage": stage,
                "message": message,
                "retry_count": int(state.get("retries", 0)) + 1,
                "timestamp": _utc_now_iso(),
            }
        )
        state["errors"] = errors
        state["retries"] = int(state.get("retries", 0)) + 1
        return self._mark(state, "system", "error", f"{stage}: {message}")

    def _add_cost(self, state: dict[str, Any], agent: str, operation: str,
                  tokens: int = 0, cost_usd: float = 0.0) -> dict[str, Any]:
        """Registra consumo de tokens/custo de um agente."""
        costs = list(state.get("costs", []) or [])
        costs.append(
            {
                "agent": agent,
                "operation": operation,
                "tokens": tokens,
                "cost_usd": cost_usd,
                "timestamp": _utc_now_iso(),
            }
        )
        state["costs"] = costs
        return self._mark(state, agent, "cost", f"{operation}: {tokens} tokens",
                          tokens=tokens, cost_usd=cost_usd)
