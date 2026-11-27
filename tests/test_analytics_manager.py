"""Testes de cobertura para src/workflow/analytics.py (CP25).

Provedores determinísticos, sem rede: MockAnalyticsProvider, RealAnalyticsProvider
(esqueleto que levanta NotImplementedError) e AnalyticsManager com idempotência e
separação de proveniência (mock|real|estimated|unavailable).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.workflow.analytics import (
    AnalyticsManager,
    MockAnalyticsProvider,
    RealAnalyticsProvider,
)
from src.workflow.state import MetricSnapshot, PublicationTarget


def _target(
    target_id: str = "target-1",
    platform: str = "tiktok",
    local_state: str = "published",
    remote_status: str = "published",
    remote_id: str = "remote-1",
    publication_confirmed_at: str = "2026-01-01T00:00:00+00:00",
) -> PublicationTarget:
    return PublicationTarget(
        target_id=target_id,
        video_id="video-1",
        platform=platform,
        channel_id="ch-tiktok",
        local_state=local_state,
        remote_status=remote_status,
        remote_id=remote_id,
        publication_confirmed_at=publication_confirmed_at,
    )


# --------------------------------------------------------------------------- #
# MockAnalyticsProvider                                                       #
# --------------------------------------------------------------------------- #
def test_mock_provider_returns_deterministic_metrics():
    provider = MockAnalyticsProvider()
    assert provider.name == "mock"
    assert provider.provenance == "mock"
    snap1 = provider.fetch_metrics(_target())
    snap2 = provider.fetch_metrics(_target())
    assert snap1.provenance == "mock"
    assert snap1.metrics == snap2.metrics  # mesmo target -> mesmo resultado
    assert snap1.target_id == snap2.target_id
    assert snap1.observed_at == snap2.observed_at


def test_mock_provider_publication_confirmed_somente_quando_confirmado():
    provider = MockAnalyticsProvider()
    # Target confirmado remoto e publicado -> publication_confirmed True
    snap = provider.fetch_metrics(_target(local_state="published", remote_status="published"))
    assert snap.publication_confirmed is True
    assert snap.platform == "tiktok"
    assert snap.publication_id == "remote-1"


def test_mock_provider_nao_confirma_sem_estado_publicado():
    provider = MockAnalyticsProvider()
    snap = provider.fetch_metrics(_target(local_state="queued", remote_status="scheduled"))
    assert snap.publication_confirmed is False
    assert snap.provenance == "mock"


def test_mock_provider_diferentes_targets_produzem_metricas_diferentes():
    provider = MockAnalyticsProvider()
    s1 = provider.fetch_metrics(_target(target_id="t-a"))
    s2 = provider.fetch_metrics(_target(target_id="t-b"))
    assert s1.metrics != s2.metrics


# --------------------------------------------------------------------------- #
# RealAnalyticsProvider (esqueleto)                                           #
# --------------------------------------------------------------------------- #
def test_real_provider_levanta_not_implementado():
    provider = RealAnalyticsProvider()
    assert provider.name == "real"
    assert provider.provenance == "real"
    with pytest.raises(NotImplementedError, match="credenciais"):
        provider.fetch_metrics(_target())


# --------------------------------------------------------------------------- #
# AnalyticsManager                                                            #
# --------------------------------------------------------------------------- #
def test_manager_collect_all_mock_provider():
    manager = AnalyticsManager(providers=[MockAnalyticsProvider()])
    snapshots = manager.collect_all([_target()])
    assert len(snapshots) == 1
    assert snapshots[0].provenance == "mock"
    assert "views" in snapshots[0].metrics


def test_manager_collect_all_varios_targets_e_provedores():
    manager = AnalyticsManager(providers=[MockAnalyticsProvider()])
    snapshots = manager.collect_all([_target(target_id="t-a"), _target(target_id="t-b")])
    assert len(snapshots) == 2
    ids = {s.target_id for s in snapshots}
    assert ids == {"t-a", "t-b"}


def test_manager_idempotencia_nao_duplica_mesmo_target_observed_at():
    manager = AnalyticsManager(providers=[MockAnalyticsProvider()])
    s1 = manager.collect_all([_target()])[0]
    # mesmo target e mesmo observed_at -> nao duplica
    s2 = manager.collect_all([_target()])[0]
    assert len(manager._snapshots) == 1
    assert s1.snapshot_id == s2.snapshot_id


def test_manager_falha_provedor_gera_unavailable_sem_parar_outros():
    @dataclass
    class _FailingProvider:
        name = "failing"
        provenance = "unavailable"

        def fetch_metrics(self, target):
            raise RuntimeError("rede fora do ar")

    manager = AnalyticsManager(providers=[_FailingProvider(), MockAnalyticsProvider()])
    snapshots = manager.collect_all([_target()])
    provenances = {s.provenance for s in snapshots}
    assert "unavailable" in provenances
    assert "mock" in provenances  # outro provedor ainda funciona


def test_manager_consolidate_adiciona_snapshots_ao_estado():
    manager = AnalyticsManager(providers=[MockAnalyticsProvider()])
    state: dict = {
        "publication_targets": [_target().model_dump(mode="json")],
        "metric_snapshots": [],
    }
    out = manager.consolidate(state)
    assert len(out["metric_snapshots"]) == 1
    assert out["metric_snapshots"][0]["provenance"] == "mock"


def test_manager_consolidate_nao_duplica_snapshot_existente():
    manager = AnalyticsManager(providers=[MockAnalyticsProvider()])
    snap = manager.collect_all([_target()])[0]
    state: dict = {
        "publication_targets": [_target().model_dump(mode="json")],
        "metric_snapshots": [snap.model_dump(mode="json")],
    }
    out = manager.consolidate(state)
    # ja existe -> nao duplica
    assert len(out["metric_snapshots"]) == 1


def test_manager_consolidate_somente_targets_publicaveis():
    manager = AnalyticsManager(providers=[MockAnalyticsProvider()])
    state: dict = {
        "publication_targets": [
            _target(local_state="failed").model_dump(mode="json"),
            _target(local_state="published").model_dump(mode="json"),
        ],
        "metric_snapshots": [],
    }
    out = manager.consolidate(state)
    # so o target publicado entra
    assert len(out["metric_snapshots"]) == 1


def test_manager_consolidate_invalido_ignorado_sem_parar():
    manager = AnalyticsManager(providers=[MockAnalyticsProvider()])
    state: dict = {
        "publication_targets": ["nao_eh_um_dicionario_valido"],
        "metric_snapshots": [],
    }
    out = manager.consolidate(state)
    # target invalido e ignorado pelo except (linhas 170-171), nao trava
    assert out["metric_snapshots"] == []


def test_manager_ausencia_de_dado_nao_equivale_a_zero():
    manager = AnalyticsManager(providers=[MockAnalyticsProvider()])
    # Provedor que falha gera snapshot com provenance unavailable e metrics vazio
    @dataclass
    class _EmptyProvider:
        name = "empty"
        provenance = "unavailable"

        def fetch_metrics(self, target):
            raise RuntimeError("sem dados")

    manager = AnalyticsManager(providers=[_EmptyProvider()])
    snapshots = manager.collect_all([_target()])
    assert snapshots[0].provenance == "unavailable"
    assert snapshots[0].metrics == {}
    assert snapshots[0].publication_confirmed is False


__all__ = []
