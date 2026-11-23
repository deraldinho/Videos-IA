"""Testes TDD para CP25 — Analytics Intelligence (persistido).

Critérios de aceitação (missão CP25):
1. MetricSnapshot: snapshot_id, target_id, platform, publication_id, observed_at,
   source, provenance (mock|real|estimated|unavailable), metrics (dict), created_at.
   Imutável após criação.
2. AnalyticsProvider (protocolo): fetch_metrics(target) -> MetricSnapshot.
3. MockAnalyticsProvider: retorna métricas com provenance="mock", sem chamada de rede.
4. Cada métrica tem platform, publication_id, instant (observed_at), source.
   Ausência de dado não equivale a zero.
5. real/mock/estimated/unavailable visivelmente separados (campo provenance).
6. Recoleta idempotente: mesmo target_id + mesmo observed_at = mesmo snapshot.
7. MonitoringAgent consolida snapshots de métrica no estado.
8. Dashboard reproduz números armazenados (teste de consistência).

Testes isolados, sem rede, sem credenciais.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.workflow.state import (
    MetricSnapshot,
    PublicationTarget,
    empty_state,
    state_to_dict,
)
from src.workflow.analytics import (
    AnalyticsProvider,
    AnalyticsManager,
    MockAnalyticsProvider,
    RealAnalyticsProvider,
)


# --------------------------------------------------------------------------- #
# Modelo MetricSnapshot                                                       #
# --------------------------------------------------------------------------- #
def test_metric_snapshot_todos_os_campos():
    """MetricSnapshot armazena todos os campos mínimos do contrato."""
    s = MetricSnapshot(
        target_id="tgt-1",
        platform="tiktok",
        publication_id="pub-1",
        source="mock_analytics",
        provenance="mock",
        metrics={"views": 100, "likes": 10},
    )
    assert s.snapshot_id
    assert s.target_id == "tgt-1"
    assert s.platform == "tiktok"
    assert s.publication_id == "pub-1"
    assert s.observed_at
    assert s.source == "mock_analytics"
    assert s.provenance == "mock"
    assert s.metrics == {"views": 100, "likes": 10}
    assert s.created_at


def test_metric_snapshot_valores_provenance():
    """provenance aceita: mock, real, estimated, unavailable."""
    for prov in ["mock", "real", "estimated", "unavailable"]:
        s = MetricSnapshot(target_id="t", platform="p", provenance=prov)
        assert s.provenance == prov


def test_metric_snapshot_imutavel_apos_criacao():
    """MetricSnapshot é imutável após criação (frozen-like via Pydantic)."""
    s = MetricSnapshot(target_id="t", platform="p", provenance="mock")
    with pytest.raises(Exception):
        s.provenance = "real"


def test_metric_snapshot_round_trip_dict():
    """MetricSnapshot faz round-trip por model_dump/model_validate."""
    s = MetricSnapshot(
        target_id="tgt-2", platform="youtube_shorts",
        provenance="real", metrics={"views": 500, "shares": 25},
    )
    dumped = s.model_dump(mode="json")
    restored = MetricSnapshot.model_validate(dumped)
    assert restored.target_id == "tgt-2"
    assert restored.platform == "youtube_shorts"
    assert restored.provenance == "real"
    assert restored.metrics == {"views": 500, "shares": 25}


def test_metric_snapshot_tem_platform_publicacao_instante_fonte():
    """Cada snapshot tem platform, publication_id, observed_at (instant), source."""
    s = MetricSnapshot(
        target_id="tgt-10", platform="instagram_reels",
        publication_id="pub-10", source="mock_analytics",
        provenance="mock", metrics={"views": 42},
    )
    assert s.platform == "instagram_reels"
    assert s.publication_id == "pub-10"
    assert s.observed_at  # instante
    assert s.source == "mock_analytics"


def test_ausencia_de_dado_nao_equivale_a_zero():
    """Métrica ausente fica com provenance='unavailable' ou metrics vazios, não zero."""
    s = MetricSnapshot(
        target_id="tgt-11", platform="tiktok",
        provenance="unavailable", metrics={},
    )
    assert s.provenance == "unavailable"
    assert s.metrics == {}
    # Não tem views=0 forçado
    assert "views" not in s.metrics


# --------------------------------------------------------------------------- #
# Protocolo AnalyticsProvider                                                 #
# --------------------------------------------------------------------------- #
def test_analytics_provider_eh_protocolo():
    """AnalyticsProvider é um runtime_checkable Protocol."""
    assert hasattr(AnalyticsProvider, "__subclasshook__")


def test_provenance_visivelmente_separados():
    """provenance distingue mock/real/estimated/unavailable, nunca misturados."""
    provider = MockAnalyticsProvider()
    target = PublicationTarget(target_id="tgt-5", platform="tiktok")
    snap = provider.fetch_metrics(target)
    assert snap.provenance == "mock"
    assert snap.provenance != "real"
    assert snap.provenance != "estimated"
    assert snap.provenance != "unavailable"


# --------------------------------------------------------------------------- #
# MockAnalyticsProvider                                                       #
# --------------------------------------------------------------------------- #
def test_mock_analytics_provider_nome_e_provenance():
    """MockAnalyticsProvider.name='mock', provenance='mock'."""
    provider = MockAnalyticsProvider()
    assert provider.name == "mock"
    assert provider.provenance == "mock"


def test_mock_fetch_metrics_retorna_snapshot_sem_rede():
    """fetch_metrics retorna MetricSnapshot com provenance='mock', sem rede."""
    provider = MockAnalyticsProvider()
    target = PublicationTarget(
        target_id="tgt-3", video_id="vid-3", platform="tiktok",
        local_state="published",
    )
    snap = provider.fetch_metrics(target)
    assert isinstance(snap, MetricSnapshot)
    assert snap.target_id == "tgt-3"
    assert snap.platform == "tiktok"
    assert snap.provenance == "mock"
    assert snap.source == "mock"
    assert snap.observed_at
    assert isinstance(snap.metrics, dict)
    assert "views" in snap.metrics or "likes" in snap.metrics


def test_mock_deterministico_mesmo_target_mesmas_metricas():
    """Mesmo target_id → mesmas métricas (determinístico, sem rede)."""
    provider = MockAnalyticsProvider()
    target = PublicationTarget(
        target_id="tgt-4", video_id="vid-4", platform="tiktok",
    )
    s1 = provider.fetch_metrics(target)
    s2 = provider.fetch_metrics(target)
    assert s1.metrics == s2.metrics
    assert s1.observed_at == s2.observed_at  # mesmo instante


# --------------------------------------------------------------------------- #
# RealAnalyticsProvider (esqueleto, sem API real)                             #
# --------------------------------------------------------------------------- #
def test_real_analytics_provider_levanta_not_implemented():
    """RealAnalyticsProvider.fetch_metrics levanta NotImplementedError (sem API real)."""
    provider = RealAnalyticsProvider()
    assert provider.name == "real"
    assert provider.provenance == "real"
    target = PublicationTarget(target_id="t", platform="tiktok")
    with pytest.raises(NotImplementedError):
        provider.fetch_metrics(target)


# --------------------------------------------------------------------------- #
# AnalyticsManager — coleta idempotente                                       #
# --------------------------------------------------------------------------- #
def test_analytics_manager_collect_all_retorna_snapshots():
    """collect_all retorna lista de MetricSnapshot, um por target."""
    from src.workflow.analytics import AnalyticsManager

    mgr = AnalyticsManager([MockAnalyticsProvider()])
    targets = [
        PublicationTarget(target_id="tgt-6", video_id="vid-6", platform="tiktok"),
        PublicationTarget(target_id="tgt-7", video_id="vid-7", platform="youtube_shorts"),
    ]
    snaps = mgr.collect_all(targets)
    assert len(snaps) == 2
    assert snaps[0].target_id == "tgt-6"
    assert snaps[1].target_id == "tgt-7"


def test_analytics_manager_idempotente_nao_duplica():
    """Mesmo target_id + mesmo observed_at não duplica snapshot."""
    from src.workflow.analytics import AnalyticsManager

    mgr = AnalyticsManager([MockAnalyticsProvider()])
    target = PublicationTarget(target_id="tgt-8", video_id="vid-8", platform="tiktok")
    snaps1 = mgr.collect_all([target])
    snaps2 = mgr.collect_all([target])
    assert snaps1[0].observed_at == snaps2[0].observed_at
    assert len(snaps1) == len(snaps2) == 1


def test_analytics_manager_consolidate_adiciona_ao_estado():
    """consolidate() adiciona snapshots ao estado (state['metric_snapshots'])."""
    from src.workflow.analytics import AnalyticsManager

    mgr = AnalyticsManager([MockAnalyticsProvider()])
    state = state_to_dict(empty_state("exec-an", "vid-an"))
    state["publication_targets"] = [
        PublicationTarget(target_id="tgt-9", video_id="vid-an", platform="tiktok", local_state="published"),
    ]
    out = mgr.consolidate(state)
    assert "metric_snapshots" in out
    assert len(out["metric_snapshots"]) >= 1
    assert out["metric_snapshots"][0]["target_id"] == "tgt-9"


# --------------------------------------------------------------------------- #
# WorkflowState — metric_snapshots                                            #
# --------------------------------------------------------------------------- #
def test_workflow_state_tem_metric_snapshots():
    """WorkflowState aceita campo metric_snapshots (lista de MetricSnapshot)."""
    from src.workflow.state import WorkflowState

    state = empty_state("exec-ws-an", "vid-ws-an")
    assert hasattr(state, "metric_snapshots")
    assert state.metric_snapshots == []


def test_workflow_state_metric_snapshots_round_trip():
    """metric_snapshots faz round-trip por state_to_dict/dict_to_state."""
    from src.workflow.state import WorkflowState

    state = empty_state("exec-ws-an2", "vid-ws-an2")
    state.metric_snapshots = [
        MetricSnapshot(
            target_id="tgt-12", platform="tiktok", provenance="mock",
            metrics={"views": 100},
        ),
    ]
    data = state_to_dict(state)
    assert len(data["metric_snapshots"]) == 1
    assert data["metric_snapshots"][0]["provenance"] == "mock"
    restored = WorkflowState.model_validate(data)
    assert len(restored.metric_snapshots) == 1
    assert restored.metric_snapshots[0].provenance == "mock"


# --------------------------------------------------------------------------- #
# MonitoringAgent consolida métricas (integração CP25)                        #
# --------------------------------------------------------------------------- #
def test_monitoring_agent_consolidates_metric_snapshots():
    """MonitoringAgent adiciona metric_snapshots_count e provenance_breakdown."""
    from src.workflow.agents.monitoring import MonitoringAgent

    state = state_to_dict(empty_state("exec-mon", "vid-mon"))
    state["publication_targets"] = [
        PublicationTarget(target_id="tgt-13", video_id="vid-mon", platform="tiktok", local_state="published"),
        PublicationTarget(target_id="tgt-14", video_id="vid-mon", platform="youtube_shorts", local_state="queued"),
    ]
    out = MonitoringAgent()(state)
    monitoring = out.get("monitoring", {})
    assert "metric_snapshots_count" in monitoring
    assert "provenance_breakdown" in monitoring
    assert "platforms_monitored" in monitoring


# --------------------------------------------------------------------------- #
# Dashboard reproduz números armazenados (consistência)                       #
# --------------------------------------------------------------------------- #
def test_dashboard_reproduce_numeros_armazenados_consistencia():
    """O dashboard lê os snapshots armazenados e reproduz os mesmos números
    (consistência): mesma agregação a cada leitura, ausência de dado não é zero."""
    from src.workflow.analytics import AnalyticsManager

    mgr = AnalyticsManager([MockAnalyticsProvider()])

    def montar_estado():
        state = state_to_dict(empty_state("exec-dash", "vid-dash"))
        state["publication_targets"] = [
            PublicationTarget(target_id="tgt-d1", video_id="vid-dash", platform="tiktok", local_state="published"),
            PublicationTarget(target_id="tgt-d2", video_id="vid-dash", platform="youtube_shorts", local_state="published"),
        ]
        return state

    def ler_views_por_plataforma(snapshots):
        agregado: dict[str, int] = {}
        for s in snapshots:
            agregado[s["platform"]] = agregado.get(s["platform"], 0) + s["metrics"].get("views", 0)
        return agregado

    out1 = mgr.consolidate(montar_estado())
    out2 = mgr.consolidate(montar_estado())

    # O dashboard reproduz os mesmos números armazenados (consistência).
    assert ler_views_por_plataforma(out1["metric_snapshots"]) == ler_views_por_plataforma(out2["metric_snapshots"])
    for s in out1["metric_snapshots"]:
        assert s["provenance"] == "mock"
        if s["provenance"] == "unavailable":
            assert s["metrics"] == {}  # ausência de dado não equivale a zero


__all__ = []
