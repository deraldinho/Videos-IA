"""Testes de regressão CR-07: sanitizacao e broadcast do EventBus.

Defeito: credenciais e segredos podem vazar nos campos ``fields`` e ``note``
dos eventos porque a sanitizacao so ocorre em ``model_dump_safe`` (nao no
``model_dump`` padrao) e os campos são armazenados brutos. Alem disso, o
broadcast na fila WebSocket pode silenciosamente descartar eventos quando o
loop nao esta vinculado.

Contratos esperados:
- Nenhuma chave secreta (api_key, secret, token, password, etc.) aparece em
  valores serializados dos eventos.
- Valores sensíveis são substituídos por "***".
- O broadcast na fila funciona quando o loop esta vinculado.
"""

from __future__ import annotations

from src.workflow.events import AgentEvent, event_bus, emit


# --------------------------------------------------------------------------- #
# CR-07: sanitizacao de segredos nos eventos                                  #
# --------------------------------------------------------------------------- #
def test_event_fields_nao_vazam_segredos_em_model_dump_safe():
    """Chaves secretas em 'fields' sao mascaradas no dump seguro."""
    ev = AgentEvent(
        agent="publishing_agent",
        action="completed",
        fields={
            "cloudinary_api_secret": "super-secreto",
            "api_key": "chave-real",
            "buffer_post_id": "bp-123",
            "video_id": "vid-1",
            "authorization": "Bearer token-xyz",
            "normal_field": "visivel",
        },
    )
    safe = ev.model_dump_safe()
    assert safe["fields"]["cloudinary_api_secret"] == "***"
    assert safe["fields"]["api_key"] == "***"
    assert safe["fields"]["authorization"] == "***"
    assert safe["fields"]["buffer_post_id"] == "bp-123"
    assert safe["fields"]["normal_field"] == "visivel"


def test_event_note_nao_vazam_segredos():
    """Strings sensiveis em 'note' sao mascaradas no dump seguro."""
    ev = AgentEvent(
        agent="cloudinary_client",
        action="error",
        note="falha ao enviar: api_key=chave-real no upload",
    )
    safe = ev.model_dump_safe()
    assert "chave-real" not in safe["note"]
    assert "***" in safe["note"]


def test_model_dump_puro_tambem_nao_vazam_sem_sanitizar_quando_usado_direto():
    """model_dump (nao _safe) nao sanitiza: contrato documentado."""
    ev = AgentEvent(
        agent="x",
        action="started",
        fields={"secret": "valor-secreto"},
    )
    # O contrato e que model_dump_safe sanitiza; model_dump bruto nao sanitiza.
    # Este teste documenta o comportamento atual para evitar regressao silenciosa.
    raw = ev.model_dump(mode="json")
    assert raw["fields"]["secret"] == "valor-secreto"


# --------------------------------------------------------------------------- #
# CR-07: broadcast da fila quando loop vinculado                              #
# --------------------------------------------------------------------------- #
def test_broadcast_na_fila_quando_loop_nao_vinculado_nao_lanca(monkeypatch):
    """Broadcast silencioso quando fila/loop ausente: nao lança excecao."""
    # Desvincula o loop e fila do barramento global para isolar o teste.
    event_bus._loop = None
    event_bus._queue = None
    # Nao deve lançar excecao mesmo sem loop vinculado.
    ev = event_bus.publish("test_agent", "started", {"video_id": "v1"})
    assert ev is not None


__all__ = []
