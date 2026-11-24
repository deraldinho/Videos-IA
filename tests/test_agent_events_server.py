"""Testes para os endpoints de visibilidade em tempo real dos agentes (CP29).

Critérios de aceitação:
- GET /api/agent-events retorna lista de eventos com contagem e último seq.
- GET /agents retorna HTML da UI de visibilidade em tempo real.
- WebSocket /api/agent-events/ws aceita conexão e recebe eventos.
- Testes isolados, sem rede, sem credenciais.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.server import app


client = TestClient(app)


def test_agent_events_endpoint_retorna_lista():
    resp = client.get("/api/agent-events?limit=10")
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert "count" in data
    assert "last_seq" in data
    assert isinstance(data["events"], list)


def test_agents_ui_retorna_html():
    resp = client.get("/agents")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    html = resp.text
    assert "Agentes em Tempo Real" in html
    assert "/api/agent-events/ws" in html


def test_agent_events_websocket_recebe_eventos():
    from src.workflow.events import event_bus, emit, ACTION_STARTED

    # Limpa o barramento global para um evento de teste limpo.
    with event_bus._lock:
        event_bus._events.clear()
        event_bus._seq = 0

    state = {"execution_id": "exec-t", "video_id": "vid-t", "status": "SCRIPT_READY"}
    emit("test_agent", ACTION_STARTED, state, note="teste de websocket")

    with client.websocket_connect("/api/agent-events/ws") as ws:
        # Deve receber o snapshot (inclui o evento emitido acima) em texto.
        received = ws.receive_text()
        import json
        ev = json.loads(received)
        assert ev["agent"] == "test_agent"
        assert ev["action"] == ACTION_STARTED
        assert ev["note"] == "teste de websocket"

    # Limpeza para não poluir outros testes (inclui desfazer bind do loop).
    with event_bus._lock:
        event_bus._events.clear()
        event_bus._seq = 0
    event_bus._queue = None
    event_bus._loop = None


__all__ = []
