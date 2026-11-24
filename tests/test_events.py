"""Testes TDD para o barramento de eventos de agentes (CP29).

Critérios de aceitação:
- AgentEvent serializa/deserializa (round-trip) com campos esperados.
- EventBus.publish armazena com sequência monotônica crescente.
- EventBus.snapshot filtra por since_seq e respeita o limite.
- Campos sensíveis (api_key, secret, token...) são filtrados nos campos/note.
- emit() é atalho para event_bus.publish.
- Testes isolados, sem rede, sem credenciais.
"""

from __future__ import annotations

import pytest

from src.workflow.events import (
    AgentEvent,
    EventBus,
    ACTION_COMPLETED,
    ACTION_ERROR,
    ACTION_NOTE,
    ACTION_STARTED,
    emit,
    event_bus,
)


def _fresh_bus() -> EventBus:
    return EventBus(maxlen=100)


# --------------------------------------------------------------------------- #
# AgentEvent                                                                  #
# --------------------------------------------------------------------------- #
def test_agent_event_campos_esperados():
    ev = AgentEvent(
        seq=1,
        agent="research_agent",
        action=ACTION_STARTED,
        execution_id="exec-1",
        video_id="vid-1",
        stage="IDEA_CREATED",
        status="IDEA_RESEARCHED",
        note="pesquisa iniciada",
        fields={"fontes": 3},
    )
    assert ev.seq == 1
    assert ev.agent == "research_agent"
    assert ev.action == ACTION_STARTED
    assert ev.execution_id == "exec-1"
    assert ev.video_id == "vid-1"
    assert ev.stage == "IDEA_CREATED"
    assert ev.status == "IDEA_RESEARCHED"
    assert ev.note == "pesquisa iniciada"
    assert ev.fields == {"fontes": 3}
    assert ev.id and ev.ts


def test_agent_event_round_trip():
    ev = AgentEvent(agent="render_agent", action=ACTION_COMPLETED, fields={"mp4": "x.mp4"})
    dumped = ev.model_dump(mode="json")
    restored = AgentEvent.model_validate(dumped)
    assert restored.agent == ev.agent
    assert restored.action == ev.action
    assert restored.fields == ev.fields


# --------------------------------------------------------------------------- #
# EventBus                                                                    #
# --------------------------------------------------------------------------- #
def test_publish_sequencia_monotonica():
    bus = _fresh_bus()
    st = {"execution_id": "exec-1", "video_id": "vid-1", "status": "SCRIPT_READY"}
    e1 = bus.publish("script_agent", ACTION_STARTED, state=st)
    e2 = bus.publish("script_agent", ACTION_COMPLETED, state=st)
    assert e2.seq == e1.seq + 1
    assert e1.seq >= 1


def test_snapshot_filtra_por_since_seq():
    bus = _fresh_bus()
    st = {"execution_id": "exec-1", "video_id": "vid-1", "status": "SCRIPT_READY"}
    bus.publish("voice_agent", ACTION_STARTED, state=st)
    bus.publish("voice_agent", ACTION_COMPLETED, state=st)
    last = bus.last_seq()
    recentes = bus.snapshot(since_seq=last - 1, limit=10)
    assert len(recentes) == 1
    assert recentes[0].seq == last


def test_snapshot_respeita_limite():
    bus = _fresh_bus()
    st = {"execution_id": "exec-1", "video_id": "vid-1", "status": "SCRIPT_READY"}
    for _ in range(5):
        bus.publish("render_agent", ACTION_STARTED, state=st)
    limite = bus.snapshot(limit=2)
    assert len(limite) == 2


def test_snapshot_mais_recentes_primeiro():
    bus = _fresh_bus()
    st = {"execution_id": "exec-1", "video_id": "vid-1", "status": "SCRIPT_READY"}
    seqs = [bus.publish("visual_agent", ACTION_STARTED, state=st).seq for _ in range(3)]
    recentes = bus.snapshot(limit=10)
    assert [e.seq for e in recentes] == sorted(seqs, reverse=True)


def test_campos_sensiveis_filtrados():
    bus = _fresh_bus()
    st = {"execution_id": "exec-1", "video_id": "vid-1", "status": "SCRIPT_READY"}
    ev = bus.publish(
        "publishing_agent",
        ACTION_ERROR,
        state=st,
        note="falhou com api_key=SECRETO",
        fields={"api_key": "SECRETO", "token": "TOKEN", "upload": "ok"},
    )
    dump = ev.model_dump_safe()
    assert dump["fields"]["api_key"] == "***"
    assert dump["fields"]["token"] == "***"
    assert dump["fields"]["upload"] == "ok"
    assert "SECRETO" not in dump["note"]


def test_emit_atalho_para_publicar():
    antes = event_bus.last_seq()
    st = {"execution_id": "exec-1", "video_id": "vid-1", "status": "SCRIPT_READY"}
    ev = emit("monitoring_agent", ACTION_NOTE, state=st, note="tick executado")
    assert ev.agent == "monitoring_agent"
    assert ev.action == ACTION_NOTE
    assert event_bus.last_seq() == antes + 1
    # limpa o evento adicionado ao barramento global para não poluir outros testes
    with event_bus._lock:
        event_bus._events.pop()
        event_bus._seq = antes


__all__ = []
