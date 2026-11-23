"""Módulo de analytics persistido (CP25).

Fornece:

* ``AnalyticsProvider`` — protocolo de provedor de métricas (fetch_metrics).
* ``MockAnalyticsProvider`` — métricas simuladas com provenance="mock", sem rede.
* ``RealAnalyticsProvider`` — esqueleto (levanta NotImplementedError sem API real).
* ``AnalyticsManager`` — coleta métricas de múltiplos provedores com idempotência,
  separação de proveniência (mock/real/estimated/unavailable) e consolidação no estado.

Regras:
- real, mock, estimated, unavailable visivelmente separados (campo provenance).
- Snapshots imutáveis após criação.
- Recoleta idempotente preserva histórico.
- Ausência de dado não equivale a zero.
- Nenhuma chamada real sem autorização.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.workflow.state import MetricSnapshot, PublicationTarget, _utc_now_iso


# --------------------------------------------------------------------------- #
# Protocolo                                                                   #
# --------------------------------------------------------------------------- #
@runtime_checkable
class AnalyticsProvider(Protocol):
    """Protocolo de provedor de métricas de analytics."""

    name: str
    provenance: str

    def fetch_metrics(self, target: PublicationTarget) -> MetricSnapshot: ...


# --------------------------------------------------------------------------- #
# MockAnalyticsProvider                                                       #
# --------------------------------------------------------------------------- #
class MockAnalyticsProvider:
    """Provedor mock de analytics: métricas simuladas, provenance="mock", sem rede.

    Valores determinísticos baseados em target_id (mesmo target → mesmas métricas).
    """

    name = "mock"
    provenance = "mock"

    def fetch_metrics(self, target: PublicationTarget) -> MetricSnapshot:
        """Retorna MetricSnapshot com métricas simuladas determinísticas."""
        # Hash simples baseado em target_id para métricas e instante determinísticos
        h = sum(ord(c) for c in target.target_id) % 1000
        views = 100 + (h * 7) % 900
        likes = max(1, views // 20)
        shares = max(0, likes // 10)
        comments = max(0, likes // 20)

        # Observed_at derivado do target_id para ser determinístico (mesmo target → mesmo instante)
        observed_at = f"2026-01-01T00:00:{h:02d}+00:00"

        return MetricSnapshot(
            target_id=target.target_id,
            platform=target.platform,
            publication_id=getattr(target, "remote_id", "") or "",
            source=self.name,
            provenance=self.provenance,
            metrics={
                "views": views,
                "likes": likes,
                "shares": shares,
                "comments": comments,
            },
            observed_at=observed_at,
        )


# --------------------------------------------------------------------------- #
# RealAnalyticsProvider (esqueleto)                                           #
# --------------------------------------------------------------------------- #
class RealAnalyticsProvider:
    """Provedor real de analytics — esqueleto.

    Levanta NotImplementedError por padrão; deve ser ativado apenas com
    credenciais e autorização explícita do operador.
    """

    name = "real"
    provenance = "real"

    def fetch_metrics(self, target: PublicationTarget) -> MetricSnapshot:
        raise NotImplementedError(
            "RealAnalyticsProvider requer credenciais e autorização explícita. "
            "Não há API real configurada por padrão."
        )


# --------------------------------------------------------------------------- #
# AnalyticsManager                                                            #
# --------------------------------------------------------------------------- #
class AnalyticsManager:
    """Coordena múltiplos AnalyticsProvider com idempotência e separação de proveniência.

    - Mesmo target_id + mesmo observed_at não duplica snapshot.
    - Separa real/mock/estimated/unavailable — nunca mistura proveniência.
    - Ausência de dado != zero (métrica ausente fica com provenance='unavailable').
    """

    def __init__(self, providers: list[AnalyticsProvider]) -> None:
        self.providers = providers
        self._by_name: dict[str, AnalyticsProvider] = {p.name: p for p in providers}
        self._snapshots: dict[str, MetricSnapshot] = {}  # target_id:observed_at -> snapshot

    def _key(self, target_id: str, observed_at: str) -> str:
        return f"{target_id}:{observed_at}"

    # -------------------------------------------------------------- collect_all
    def collect_all(
        self,
        targets: list[PublicationTarget],
    ) -> list[MetricSnapshot]:
        """Coleta métricas de todos os targets via todos os provedores."""
        snapshots: list[MetricSnapshot] = []
        for target in targets:
            for provider in self.providers:
                try:
                    snap = provider.fetch_metrics(target)
                    key = self._key(snap.target_id, snap.observed_at)
                    # Idempotência: não duplica para mesmo target+observed_at
                    if key in self._snapshots:
                        snapshots.append(self._snapshots[key])
                    else:
                        self._snapshots[key] = snap
                        snapshots.append(snap)
                except Exception as exc:  # noqa: BLE001
                    # Falha em um provedor não impede outros; registra unavailable
                    snapshots.append(
                        MetricSnapshot(
                            target_id=target.target_id,
                            platform=target.platform,
                            publication_id=getattr(target, "remote_id", "") or "",
                            source=getattr(provider, "name", "unknown"),
                            provenance="unavailable",
                            metrics={},
                        )
                    )
        return snapshots

    # -------------------------------------------------------------- consolidate
    def consolidate(
        self,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """Adiciona snapshots ao estado (state['metric_snapshots'])."""
        targets: list[PublicationTarget] = []
        for t in state.get("publication_targets") or []:
            try:
                targets.append(PublicationTarget.model_validate(t))
            except Exception:  # noqa: BLE001
                continue

        # Coleta métricas apenas para targets publicados/queued
        publishable = [t for t in targets if t.local_state in ("queued", "published", "uploaded")]
        snapshots = self.collect_all(publishable)

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


__all__ = [
    "AnalyticsProvider",
    "MockAnalyticsProvider",
    "RealAnalyticsProvider",
    "AnalyticsManager",
]
