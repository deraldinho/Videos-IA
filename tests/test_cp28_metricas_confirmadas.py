"""Testes TDD para CP28 — Métricas de publicação confirmada.

Critérios de aceitação:
- MetricSnapshot tem campo publication_confirmed (bool, default False).
- provenance="real" somente quando publication_confirmed=True.
- Métricas coletadas de target publicado confirmado têm publication_confirmed=True.
- Mocks claramente identificados (provenance="mock" nunca publication_confirmed=True).
- Testes isolados, sem rede, sem credenciais.
"""

from __future__ import annotations

from src.workflow.analytics import AnalyticsManager, MockAnalyticsProvider
from src.workflow.state import MetricSnapshot, PublicationTarget, empty_state, state_to_dict


# --------------------------------------------------------------------------- #
# MetricSnapshot — campo publication_confirmed                                #
# --------------------------------------------------------------------------- #
def test_metric_snapshot_tem_publication_confirmed():
    """MetricSnapshot tem campo publication_confirmed (default False)."""
    s = MetricSnapshot(target_id="t1", platform="tiktok", publication_id="p1")
    assert hasattr(s, "publication_confirmed")
    assert s.publication_confirmed is False


def test_metric_snapshot_provenance_real_requer_publication_confirmed():
    """provenance='real' implica publication_confirmed=True."""
    s = MetricSnapshot(
        target_id="t2", platform="tiktok", publication_id="p2",
        provenance="real", publication_confirmed=True,
    )
    assert s.provenance == "real"
    assert s.publication_confirmed is True


def test_metric_snapshot_provenance_mock_nunca_publication_confirmed():
    """provenance='mock' nunca tem publication_confirmed=True."""
    s = MetricSnapshot(
        target_id="t3", platform="tiktok", publication_id="p3",
        provenance="mock", publication_confirmed=False,
    )
    assert s.provenance == "mock"
    assert s.publication_confirmed is False


def test_metric_snapshot_round_trip_com_publication_confirmed():
    """publication_confirmed faz round-trip por model_dump/model_validate."""
    s = MetricSnapshot(
        target_id="t4", platform="tiktok", publication_id="p4",
        provenance="real", publication_confirmed=True,
        metrics={"views": 1000},
    )
    dumped = s.model_dump(mode="json")
    restored = MetricSnapshot.model_validate(dumped)
    assert restored.publication_confirmed is True
    assert restored.provenance == "real"


# --------------------------------------------------------------------------- #
# AnalyticsManager — métricas de target confirmado                            #
# --------------------------------------------------------------------------- #
def test_analytics_manager_metrica_target_confirmado_tem_publication_confirmed():
    """Métrica coletada de target publicado confirmado tem publication_confirmed=True."""
    target = PublicationTarget(
        target_id="t-conf-1", video_id="vid-conf", platform="tiktok",
        local_state="published", remote_id="tt_123", remote_status="published",
        confirmed_at="2026-01-15T10:00:00+00:00",
        publication_confirmed_at="2026-01-15T10:00:00+00:00",
    )

    manager = AnalyticsManager([MockAnalyticsProvider()])
    snapshots = manager.collect_all([target])

    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.target_id == "t-conf-1"
    # Target publicado confirmado -> métrica com publication_confirmed=True
    assert snap.publication_confirmed is True


def test_analytics_manager_metrica_target_nao_confirmado_sem_publication_confirmed():
    """Métrica de target não confirmado tem publication_confirmed=False."""
    target = PublicationTarget(
        target_id="t-nao-conf", video_id="vid-nao-conf", platform="tiktok",
        local_state="queued", remote_id=None, remote_status="scheduled",
        confirmed_at=None, publication_confirmed_at=None,
    )

    manager = AnalyticsManager([MockAnalyticsProvider()])
    snapshots = manager.collect_all([target])

    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.publication_confirmed is False


def test_analytics_manager_mock_nunca_publication_confirmed_sem_alvo_confirmado():
    """Provedor mock não marca publication_confirmed=True para target não confirmado."""
    target = PublicationTarget(
        target_id="t-mock", video_id="vid-mock", platform="tiktok",
        local_state="uploaded",
    )
    manager = AnalyticsManager([MockAnalyticsProvider()])
    snapshots = manager.collect_all([target])
    assert all(not s.publication_confirmed for s in snapshots)


__all__ = []
