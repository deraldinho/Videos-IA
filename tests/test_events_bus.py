"""Testes de cobertura para src/workflow/events.py (EventBus).

Sem servidor real, sem WebSocket real: testa snapshot, last_seq e next_event
sem loop vinculado (RuntimeError esperado).
"""

import asyncio

from src.workflow.events import event_bus, emit


def test_event_bus_snapshot_retorna_eventos():
    event_bus.publish(agent="test", action="started", state={"video_id": "v-1"})
    evs = event_bus.snapshot(since_seq=0, limit=10)
    assert len(evs) >= 1
    assert evs[0].action == "started"
    assert evs[0].video_id == "v-1"


def test_event_bus_last_seq_monotonico():
    s1 = event_bus.last_seq()
    event_bus.publish(agent="test", action="completed", state={"video_id": "v-2"})
    s2 = event_bus.last_seq()
    assert s2 > s1


def test_event_bus_next_event_sem_loop_levanta():
    # EventBus global nunca vinculado a um loop -> next_event levanta RuntimeError
    with asyncio.Runner() as runner:
        try:
            runner.run(event_bus.next_event())
            assert False, "esperava RuntimeError"
        except RuntimeError as exc:
            assert "nao vinculado" in str(exc) or "EventBus" in str(exc)


def test_emit_filtra_campos_sensiveis():
    ev = emit(
        agent="test",
        action="note",
        note="conexao com api_key=SECRETA123",
        fields={"token": "abc", "video_id": "v-1"},
    )
    dumped = ev.model_dump_safe()
    assert "SECRETA123" not in str(dumped)
    assert dumped["fields"]["token"] == "***"


__all__ = []
