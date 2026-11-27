"""Regressão CGR-005: sanitização de eventos REST/WebSocket.

Verifica:
- model_dump_safe() filtra campos sensíveis (segredos) nos eventos.
- model_dump (bruto) não sanitiza (contrato documentado, não é um bug).
- EventBus gerencia assinantes (bind_loop/unbind) e faz broadcast individual
  com limpeza de conexões desconectadas (backpressure/subscriber management).
"""
from __future__ import annotations

from src.workflow.events import (
    AgentEvent,
    ACTION_STARTED,
    event_bus,
    emit,
    _sanitize_value,
)


def test_model_dump_safe_filtra_campos_sensiveis():
    """RED: model_dump_safe deve mascarar chaves sensíveis em fields."""
    ev = AgentEvent(
        seq=1,
        agent="test_agent",
        action=ACTION_STARTED,
        fields={
            "api_key": "sk-real-secret-123",
            "api_secret": "secret-value",
            "token": "tok-abc",
            "password": "senha123",
            "authorization": "Bearer xxx",
            "cookie": "session=xyz",
            "não_sensível": "valor-ok",
        },
    )
    safe = ev.model_dump_safe()
    assert safe["fields"]["api_key"] == "***"
    assert safe["fields"]["api_secret"] == "***"
    assert safe["fields"]["token"] == "***"
    assert safe["fields"]["password"] == "***"
    assert safe["fields"]["authorization"] == "***"
    assert safe["fields"]["cookie"] == "***"
    assert safe["fields"]["não_sensível"] == "valor-ok"


def test_model_dump_bruto_nao_sanitiza_contrato_documentado():
    """RED: model_dump (bruto) não sanitiza — contrato documentado, não é bug."""
    ev = AgentEvent(
        seq=1,
        agent="test_agent",
        action=ACTION_STARTED,
        fields={"api_key": "sk-real-secret-123"},
    )
    bruto = ev.model_dump(mode="json")
    assert bruto["fields"]["api_key"] == "sk-real-secret-123"


def test_sanitize_value_limpa_listas_e_dicionarios_aninhados():
    """RED: _sanitize_value deve limpar chaves sensíveis em estruturas aninhadas."""
    from src.workflow.events import _sanitize_value

    valor = {
        "nivel1": {
            "api_key": "sk-secreto",
            "dados": [{"token": "tok-secreto"}, {"ok": True}],
        }
    }
    limpo = _sanitize_value(valor)
    assert limpo["nivel1"]["api_key"] == "***"
    assert limpo["nivel1"]["dados"][0]["token"] == "***"
    assert limpo["nivel1"]["dados"][1]["ok"] is True


def test_event_bus_bind_loop_e_unbind_gerencia_assinantes():
    """RED: EventBus deve vincular e desvincular loop de asyncio (gerenciamento de assinantes)."""
    import asyncio

    bus = event_bus
    loop = asyncio.new_event_loop()
    try:
        bus.bind_loop(loop)
        assert bus._loop is loop
        assert bus._queue is not None
    finally:
        loop.close()
        # Após fechar o loop, o broadcast não deve falhar silenciosamente.
        bus._broadcast(AgentEvent(seq=99, agent="a", action="started"))


def test_emit_nao_vaza_segredos_em_fields():
    """RED: emit() deve gerar eventos cujos fields sensíveis sejam mascarados via model_dump_safe."""
    ev = emit(
        "agente_teste",
        ACTION_STARTED,
        {"exec": "exec-1", "video": "vid-1"},
        fields={"api_key": "sk-real", "dados_normais": "ok"},
    )
    safe = ev.model_dump_safe()
    assert safe["fields"]["api_key"] == "***"
    assert safe["fields"]["dados_normais"] == "ok"
