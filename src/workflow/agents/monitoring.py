"""Agente de monitoramento: rastreia execução, métricas e logs do pipeline."""

from __future__ import annotations

from typing import Any

from src.workflow.agents.base import BaseAgent
from src.workflow.analytics import AnalyticsManager, MockAnalyticsProvider
from src.workflow.state import MetricSnapshot, PublicationTarget, Stage


def consolidate_metrics(state: dict[str, Any],
                        providers: list[Any]) -> dict[str, Any]:
    """Consolida snapshots de mérica para todos os publication_targets publicados.

    Usa ``AnalyticsManager.collect_all`` para gerar snapshots de todos os
    ``publication_targets`` com ``local_state`` em (queued, published, uploaded),
    adicionando-os ao estado em ``state["metric_snapshots"]`` de forma idempotente
    (não duplica para mesmo target_id + observed_at).

    Args:
        state: Estado compartilhado do workflow.
        providers: Lista de provedores de analytics (MockAnalyticsProvider por
            default — nenhuma chamada de rede real sem autorização).

    Returns:
        O estado mutado com ``metric_snapshots`` atualizado.
    """
    manager = AnalyticsManager(providers)
    targets: list[PublicationTarget] = []
    for t in state.get("publication_targets") or []:
        try:
            targets.append(PublicationTarget.model_validate(t))
        except Exception:  # noqa: BLE001
            continue

    # Coleta métricas apenas para targets publicados/queued/uploaded
    publishable = [t for t in targets if t.local_state in ("queued", "published", "uploaded")]
    snapshots = manager.collect_all(publishable)

    existing = list(state.get("metric_snapshots") or [])
    existing_keys = {
        f"{s.get('target_id')}:{s.get('observed_at')}" for s in existing
    }
    for s in snapshots:
        key = f"{s.target_id}:{s.observed_at}"
        if key not in existing_keys:
            existing.append(s.model_dump(mode="json"))
            existing_keys.add(key)

    state["metric_snapshots"] = existing
    return state


class MonitoringAgent(BaseAgent):
    """Monitora a execução do pipeline e agrega métricas.

    Responsabilidades (CP10 + CP25):
    - Consome todo o estado (custos, erros, auditoria, publicação).
    - Produz ``monitoring`` com resumo de execução, métricas e alertas.
    - CP25: consolida MetricSnapshot via AnalyticsManager e agrega
      metric_snapshots_count, platforms_monitored e provenance_breakdown.
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

        # --- CP25: consolida métricas via AnalyticsManager ----------------
        state = consolidate_metrics(state, [MockAnalyticsProvider()])

        snapshots = state.get("metric_snapshots") or []
        monitoring["metric_snapshots_count"] = len(snapshots)
        platforms: set[str] = set()
        breakdown: dict[str, int] = {"mock": 0, "real": 0, "estimated": 0, "unavailable": 0}
        for s in snapshots:
            platforms.add(s.get("platform", ""))
            prov = s.get("provenance", "unknown")
            breakdown[prov] = breakdown.get(prov, 0) + 1
        monitoring["platforms_monitored"] = sorted(platforms)
        monitoring["provenance_breakdown"] = breakdown

        state["monitoring"] = monitoring
        state["current_stage"] = "MONITORED"
        state["status"] = "MONITORED"
        state["_next"] = "supervisor"
        state["_step"] = "monitoring_agent"
        self._add_cost(state, self.name, "monitoramento", tokens=5, cost_usd=0.0)
        return self._mark(state, self.name, "monitor",
                          f"Status={status}, erros={error_count}, custo=${total_cost:.6f}",
                          video_id=video_id, metrics=monitoring)
