"""Event bus de agentes para visibilidade em tempo real do pipeline.

Capture eventos dos agentes especializados do grafo LangGraph (início,
término, erros, transições e anotações) e disponibiliza-os:

* para consumo síncrono (snapshot com cursor de sequência) via REST;
* para push assíncrono em tempo real via WebSocket (fila + loop de evento).

Nunca grava credenciais nos campos de evento; campos sensíveis são filtrados.
"""

from __future__ import annotations

import threading
import uuid
from collections import deque
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

# Ações padronizadas de evento de agente.
ACTION_STARTED = "started"
ACTION_COMPLETED = "completed"
ACTION_ERROR = "error"
ACTION_TRANSITION = "transition"
ACTION_NOTE = "note"

# Chaves que nunca devem aparecer nos campos de evento (filtro de segredos).
_SECRET_KEYS = {
    "api_key", "api_secret", "secret", "token", "password",
    "nvidia_api_key", "cloudinary_api_secret", "buffer_api_key",
    "authorization", "cookie",
}


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("***" if k.lower() in _SECRET_KEYS else _sanitize_value(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_sanitize_value(v) for v in value)
    return value


class AgentEvent(BaseModel):
    """Um evento emitido por um agente durante a execução do grafo."""

    model_config = {"extra": "forbid"}

    seq: int = Field(default=0, description="Sequência monotônica (cursor de paginação).")
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts: str = Field(default_factory=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat())
    execution_id: str = Field(default="")
    video_id: str = Field(default="")
    agent: str = Field(default="")
    action: str = Field(default="")  # started | completed | error | transition | note
    stage: Optional[str] = None
    status: Optional[str] = None
    note: Optional[str] = None
    fields: dict[str, Any] = Field(default_factory=dict)

    def model_dump_safe(self) -> dict[str, Any]:
        """Serializa para dicionário com campos sensíveis filtrados."""
        data = self.model_dump(mode="json")
        data["fields"] = _sanitize_value(data.get("fields") or {})
        if data.get("note"):
            data["note"] = _sanitize_str(data["note"])
        return data


def _sanitize_str(s: str) -> str:
    import re
    return re.sub(
        r'(api[_-]?key|secret|token|password|authorization)["\']?\s*[:=]\s*["\']?[^\s"&\']+',
        r'\1=***',
        s,
        flags=re.IGNORECASE,
    )


class EventBus:
    """Barra de eventos thread-safe com cursor de sequência e broadcast assíncrono.

    ``publish`` pode ser chamado de qualquer thread (grafo roda síncrono). Quando
    um loop de asyncio e uma fila estão vinculados (servidor FastAPI), o evento
    é encaminhado para a fila para push imediato aos clientes WebSocket.
    """

    def __init__(self, maxlen: int = 2000) -> None:
        self._maxlen = maxlen
        self._events: deque[AgentEvent] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0
        self._loop = None
        self._queue = None  # asyncio.Queue[AgentEvent] quando vinculado

    # ------------------------------------------------------------------ publicação
    def publish(
        self,
        agent: str,
        action: str,
        state: dict[str, Any] | None = None,
        *,
        note: Optional[str] = None,
        stage: Optional[str] = None,
        status: Optional[str] = None,
        fields: Optional[dict[str, Any]] = None,
    ) -> AgentEvent:
        """Emite e armazena um evento; encaminha para a fila de broadcast, se houver."""
        execution_id = ""
        video_id = ""
        ev_stage = stage
        ev_status = status
        if state is not None:
            execution_id = str(state.get("execution_id", "") or "")
            video_id = str(state.get("video_id", "") or "")
            if ev_stage is None:
                ev_stage = state.get("current_stage") or state.get("status")
            if ev_status is None:
                ev_status = state.get("status")

        event = AgentEvent(
            seq=self._next_seq(),
            execution_id=execution_id,
            video_id=video_id,
            agent=agent,
            action=action,
            stage=str(ev_stage) if ev_stage is not None else None,
            status=str(ev_status) if ev_status is not None else None,
            note=note,
            fields=dict(fields or {}),
        )

        with self._lock:
            self._events.append(event)

        self._broadcast(event)
        return event

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def _broadcast(self, event: AgentEvent) -> None:
        if self._queue is None or self._loop is None:
            return
        try:
            if not self._loop.is_running():
                return
            import asyncio

            asyncio.run_coroutine_threadsafe(self._queue.put(event), self._loop)
        except Exception:  # loop fechado ou indisponível: ignora o push
            pass

    # ------------------------------------------------------------------ consumo
    def bind_loop(self, loop) -> None:
        """Vincula o loop de asyncio do servidor para habilitar push WebSocket."""
        import asyncio

        self._loop = loop
        self._queue = asyncio.Queue()

    def snapshot(self, since_seq: int = 0, limit: int = 200) -> list[AgentEvent]:
        """Retorna eventos com seq > since_seq, mais recentes primeiro (limitado)."""
        with self._lock:
            events = [e for e in self._events if e.seq > since_seq]
        events.sort(key=lambda e: e.seq, reverse=True)
        return events[:limit]

    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    async def next_event(self) -> AgentEvent:
        """Aguarda o próximo evento da fila de broadcast."""
        if self._queue is None:
            raise RuntimeError("EventBus não vinculado a um loop de asyncio")
        return await self._queue.get()


# Instância global do barramento de eventos.
event_bus = EventBus()


def emit(
    agent: str,
    action: str,
    state: Optional[dict[str, Any]] = None,
    *,
    note: Optional[str] = None,
    stage: Optional[str] = None,
    status: Optional[str] = None,
    fields: Optional[dict[str, Any]] = None,
) -> AgentEvent:
    """Atalho para publicar um evento no barramento global."""
    return event_bus.publish(
        agent=agent,
        action=action,
        state=state,
        note=note,
        stage=stage,
        status=status,
        fields=fields,
    )


__all__ = [
    "AgentEvent",
    "EventBus",
    "event_bus",
    "emit",
    "ACTION_STARTED",
    "ACTION_COMPLETED",
    "ACTION_ERROR",
    "ACTION_TRANSITION",
    "ACTION_NOTE",
]
