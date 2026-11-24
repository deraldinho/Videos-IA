"""Servidor web FastAPI para UI ao vivo do TikTok AI Autopilot.

Reutiliza o Orchestrator, Database, health checks e métricas existentes.
Não altera nenhum comando CLI existente.

Endpoints:
  GET  /                        — info da API + docs
  GET  /api/videos              — lista vídeos (query: ?status=, ?limit=)
  GET  /api/videos/{video_id}   — detalhe de um vídeo
  GET  /api/stats               — agregados por status
  GET  /api/health              — health check (FFmpeg, banco, credenciais)
  GET  /api/metrics             — métricas operacionais
  WS   /api/ws                  — WebSocket para atualizações em tempo real

POST:
  POST  /api/videos                        — gerar novo vídeo
  POST  /api/videos/{id}/approve           — aprovar vídeo (AWAITING_APPROVAL → APPROVED)
  POST  /api/videos/{id}/reject            — rejeitar vídeo (→ FAILED)
  POST  /api/videos/{id}/retry             — limpar erro de vídeo em FAILED
  POST  /api/advance/{id}/{stage}          — avançar estágio específico

Autenticação (opcional): header `X-API-Key` com valor da env `UI_API_KEY`.
Se `UI_API_KEY` não estiver definida, a autenticação é desabilitada.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from src.database import ALLOWED_TRANSITIONS, Database, VIDEO_STATES
from src.errors import PipelineError
from src.graph_orchestrator import GraphOrchestrator
from src.health import run_health_checks
from src.metrics import metrics
from src.orchestrator import STAGE_ORDER  # mantido apenas para validação de estágios na API
from src.workflow.events import AgentEvent, event_bus


# ---------------------------------------------------------------------------
# Autenticação opcional por API key
# ---------------------------------------------------------------------------
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

UI_API_KEY = os.environ.get("UI_API_KEY", "")


async def verify_api_key(api_key: str | None = Depends(API_KEY_HEADER)) -> None:
    """Valida a API key quando UI_API_KEY está configurada."""
    if UI_API_KEY and api_key != UI_API_KEY:
        raise HTTPException(status_code=401, detail="API key inválida")


# ---------------------------------------------------------------------------
# Aplicação FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(title="TikTok AI Autopilot — UI ao Vivo", version="1.0.0")

# Orchestrator compartilhado (singleton). Database não mantém conexão persistente
# (abre/fecha por transação), então é seguro reutilizar entre requisições.
_orchestrator: GraphOrchestrator | None = None


def get_orchestrator() -> GraphOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = GraphOrchestrator()
    return _orchestrator


# ---------------------------------------------------------------------------
# Modelos de resposta
# ---------------------------------------------------------------------------
class VideoOut(dict):
    """Resumo de um vídeo para listagem."""

    def __init__(self, row: Any) -> None:
        super().__init__(
            video_id=row["video_id"],
            status=row["status"],
            topic=row["topic"],
            template=row["template"],
            kind=row["kind"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class VideoDetail(dict):
    """Detalhe completo de um vídeo."""

    def __init__(self, row: Any) -> None:
        super().__init__(
            video_id=row["video_id"],
            execution_id=row["execution_id"],
            status=row["status"],
            topic=row["topic"],
            template=row["template"],
            kind=row["kind"],
            title=row["title"],
            description=row["description"],
            total_seconds=row["total_seconds"],
            mp4_path=row["mp4_path"],
            cloudinary_url=row["cloudinary_url"],
            buffer_post_id=row["buffer_post_id"],
            buffer_post_state=row["buffer_post_state"],
            error_message=row["error_message"],
            retry_count=row["retry_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ---------------------------------------------------------------------------
# Modelos de request para endpoints POST
# ---------------------------------------------------------------------------
class GenerateRequest(BaseModel):
    topic: str = Field(..., description="Tema do vídeo")
    kind: str = Field(default="factual", description="factual ou ficcao")
    template: str = Field(default="curiosidade", description="curiosidade, lista, historia, explicacao")
    target_seconds: int | None = Field(default=None, ge=1, le=300, description="Duração alvo em segundos")
    execution_id: str | None = Field(default=None, description="ID de execução para retomada idempotente")


class AdvanceRequest(BaseModel):
    stage: str = Field(..., description="Estágio a avançar: script, tts, render, validate, upload, publish")
    actor: str = Field(default="operador", description="Quem está executando a ação")


class ApproveRequest(BaseModel):
    actor: str = Field(default="operador", description="Quem está aprovando")


class RejectRequest(BaseModel):
    motivo: str = Field(default="rejeitado via UI", description="Motivo da reprovação")
    actor: str = Field(default="operador", description="Quem está rejeitando")


# ---------------------------------------------------------------------------
# Endpoints GET
# ---------------------------------------------------------------------------
@app.get("/api/videos", dependencies=[Depends(verify_api_key)])
def list_videos(
    status: str | None = Query(default=None, description="Filtrar por status"),
    limit: int = Query(default=50, ge=1, le=500, description="Limite de resultados"),
) -> dict[str, Any]:
    """Lista vídeos, agrupados por status quando não há filtro."""
    orch = get_orchestrator()
    statuses = list(VIDEO_STATES) if status is None else [status]
    videos: list[dict[str, Any]] = []
    for s in statuses:
        rows = orch.list_by_status(s)
        for r in rows[:limit]:
            videos.append(VideoOut(r))
    return {"videos": videos, "count": len(videos)}


@app.get("/api/videos/{video_id}", dependencies=[Depends(verify_api_key)])
def get_video(video_id: str) -> dict[str, Any]:
    """Retorna o detalhe de um vídeo ou HTTP 404."""
    orch = get_orchestrator()
    row = orch.db.get_video(video_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Vídeo não encontrado: {video_id}")
    return VideoDetail(row)


@app.get("/api/stats", dependencies=[Depends(verify_api_key)])
def stats() -> dict[str, Any]:
    """Agregados: total de vídeos e contagem por status."""
    orch = get_orchestrator()
    by_status: dict[str, int] = {}
    total = 0
    for s in VIDEO_STATES:
        rows = orch.list_by_status(s)
        if rows:
            by_status[s] = len(rows)
            total += len(rows)
    return {"total": total, "by_status": by_status}


@app.get("/api/health", dependencies=[Depends(verify_api_key)])
def health() -> dict[str, Any]:
    """Health check do sistema (FFmpeg, banco, credenciais)."""
    report = run_health_checks()
    data = report.to_dict()
    return {"healthy": data["healthy"], "checks": data["checks"]}


@app.get("/api/metrics", dependencies=[Depends(verify_api_key)])
def get_metrics() -> dict[str, Any]:
    """Métricas operacionais (counters, gauges, uptime)."""
    snap = metrics.snapshot()
    return {
        "uptime_seconds": snap.get("uptime_seconds", 0),
        "counters": snap.get("counters", {}),
        "gauges": snap.get("gauges", {}),
    }


@app.get("/api/agent-events", dependencies=[Depends(verify_api_key)])
def list_agent_events(
    since_seq: int = Query(default=0, ge=0, description="Retorna apenas eventos com seq > since_seq"),
    limit: int = Query(default=200, ge=1, le=1000, description="Limite de eventos retornados"),
) -> dict[str, Any]:
    """Lista eventos recentes de agentes (visibilidade em tempo real do pipeline)."""
    events = event_bus.snapshot(since_seq=since_seq, limit=limit)
    return {
        "events": [e.model_dump_safe() for e in events],
        "count": len(events),
        "last_seq": event_bus.last_seq(),
    }


# ---------------------------------------------------------------------------
# WebSocket — atualizações em tempo real
# ---------------------------------------------------------------------------
class ConnectionManager:
    """Gerencia conexões WebSocket ativas e faz broadcast de mensagens."""

    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active:
            self.active.remove(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        text = json.dumps(message, ensure_ascii=False)
        disconnected: list[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_text(text)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)

    async def send_status_changed(
        self, video_id: str, from_status: str, to_status: str, actor: str, note: str
    ) -> None:
        await self.broadcast(
            {
                "type": "video.status_changed",
                "video_id": video_id,
                "from_status": from_status,
                "to_status": to_status,
                "actor": actor,
                "note": note,
                "timestamp": datetime.now(timezone).isoformat(),
            }
        )


manager = ConnectionManager()


@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket para atualizações em tempo real do pipeline.

    Quando UI_API_KEY está definida, exige ?token=<key> na query string.
    """
    if UI_API_KEY:
        token = websocket.query_params.get("token")
        if token != UI_API_KEY:
            await websocket.close(code=4001, reason="API key inválida")
            return
    await manager.connect(websocket)
    try:
        while True:
            # Mantém a conexão aberta; aguarda mensagens do cliente (ping/keepalive)
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ---------------------------------------------------------------------------
# WebSocket — eventos de agentes em tempo real (CP29)
# ---------------------------------------------------------------------------
@app.websocket("/api/agent-events/ws")
async def agent_events_ws(websocket: WebSocket) -> None:
    """WebSocket que push eventos de agentes em tempo real para clientes conectados.

    Segue o mesmo modelo de autenticação opcional de /api/ws (?token=UI_API_KEY).
    Envia o snapshot dos eventos recentes ao conectar e, em seguida, push de cada
    novo evento à medida que é emitido pelo grafo.
    """
    if UI_API_KEY:
        token = websocket.query_params.get("token")
        if token != UI_API_KEY:
            await websocket.close(code=4001, reason="API key inválida")
            return
    # Vincula o loop de asyncio do servidor ao barramento (na primeira conexão).
    if event_bus._queue is None:
        event_bus.bind_loop(asyncio.get_running_loop())
    await websocket.accept()
    try:
        # Snapshot dos eventos recentes.
        for ev in event_bus.snapshot(limit=200):
            try:
                await websocket.send_text(ev.model_dump_safe())
            except Exception:
                return
        # Push de novos eventos em tempo real.
        while True:
            try:
                ev = await event_bus.next_event()
                await websocket.send_text(ev.model_dump_safe())
            except WebSocketDisconnect:
                break
            except Exception:
                break
    finally:
        # next_event bloqueia; desconexão encerra o loop acima.
        pass


# ---------------------------------------------------------------------------
# UI — visibilidade em tempo real dos agentes (/agents)
# ---------------------------------------------------------------------------
@app.get("/agents")
def agents_ui() -> str:
    """Página que exibe, em tempo real, o que cada agente está fazendo e dizendo."""
    return Response(content="""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TikTok AI Autopilot — Agentes em Tempo Real</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background: #0b0c10; color: #e8e8f0; }
  .card { background: #12141d; border: 1px solid #2a2a3a; border-radius: 8px; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
  .row-started { border-left: 3px solid #3b82f6; }
  .row-completed { border-left: 3px solid #22c55e; }
  .row-error { border-left: 3px solid #ef4444; }
  .row-note { border-left: 3px solid #a855f7; }
  .row-transition { border-left: 3px solid #f59e0b; }
  .badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 11px; font-weight: 700; text-transform: uppercase; }
  .bg-started { background: #1e3a8a; color: #93c5fd; }
  .bg-completed { background: #14532d; color: #bbf7d0; }
  .bg-error { background: #7f1d1d; color: #fecaca; }
  .bg-note { background: #5b21b6; color: #e9d5ff; }
  .bg-transition { background: #713f12; color: #fcd34d; }
  #log { scroll-behavior: smooth; }
  .pulse::before { content: ''; display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #22c55e; animation: pulse 1.5s infinite; margin-right: 6px; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
</style>
</head>
<body class="min-h-screen p-4">
<nav class="border-b border-[#2a2a3a] bg-[#0b0c10] shadow-sm mb-6">
  <div class="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
    <div class="flex items-center gap-3">
      <div class="w-8 h-8 rounded bg-[#12141d] flex items-center justify-center">
        <svg class="w-5 h-5 text-[#e8e8f0]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h1m5-3h1m-5-3h1M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
        </svg>
      </div>
      <div>
        <h1 class="text-lg font-bold">TikTok AI Autopilot</h1>
        <p class="text-xs text-[#6b7280]">Agentes em tempo real — o que cada agente está fazendo e dizendo</p>
      </div>
    </div>
    <div class="flex items-center gap-3">
      <div class="flex items-center gap-2">
        <span class="text-xs text-[#6b7280]">Eventos:</span>
        <span id="count" class="mono text-[#3b82f6]">0</span>
      </div>
      <div class="flex items-center gap-2">
        <span class="text-xs text-[#6b7280]">Último seq:</span>
        <span id="lastSeq" class="mono text-[#3b82f6]">-</span>
      </div>
      <a href="/" class="text-xs text-[#3b82f6] hover:underline">Voltar à API</a>
    </div>
  </div>
</nav>

<main class="max-w-7xl mx-auto">
  <div class="card p-4 mb-4">
    <h2 class="font-semibold mb-2">Log de agentes (tempo real)</h2>
    <p class="text-xs text-[#6b7280] mb-3">Conectado via WebSocket a <code class="mono text-[#3b82f6]">/api/agent-events/ws</code>. Cada linha é um evento emitido por um agente do grafo LangGraph.</p>
    <div id="log" class="max-h-[70vh] overflow-y-auto border border-[#2a2a3a] rounded p-3 bg-[#0b0c10] space-y-2"></div>
    <div class="mt-3 text-xs text-[#6b7280]">Sem eventos ainda. Execute o pipeline (ex.: <code class="mono">python main.py generate</code> ou via API) para ver os agentes em ação.</div>
  </div>
</main>

<script>
(function () {
  var log = document.getElementById('log');
  var countEl = document.getElementById('count');
  var lastSeqEl = document.getElementById('lastSeq');
  var count = 0;
  var lastSeq = 0;
  var wsUrl = (window.location.protocol === 'https:' ? 'wss:' : 'ws:') +
              '//' + window.location.host + '/api/agent-events/ws';
  var token = new URLSearchParams(window.location.search).get('token');
  if (token) { wsUrl += '?token=' + encodeURIComponent(token); }

  var ws = new WebSocket(wsUrl);
  var status = document.createElement('div');
  status.className = 'text-xs text-[#6b7280] mb-2';
  log.parentNode.insertBefore(status, log);

  function htmlEscape(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function rowClass(action) {
    var map = { started: 'row-started bg-started', completed: 'row-completed bg-completed',
                error: 'row-error bg-error', note: 'row-note bg-note', transition: 'row-transition bg-transition' };
    return map[action] || 'row-started';
  }
  function append(ev) {
    count++;
    lastSeq = ev.seq;
    countEl.textContent = count;
    lastSeqEl.textContent = lastSeq;
    var cls = rowClass(ev.action);
    var fieldsHtml = '';
    if (ev.fields && Object.keys(ev.fields).length) {
      fieldsHtml = ' <span class="text-[#6b7280] text-xs mono">[' +
        Object.entries(ev.fields).map(function (kv) { return kv[0] + '=' + htmlEscape(String(kv[1])); }).join(', ') +
        ']</span>';
    }
    var noteHtml = ev.note ? ' — <span class="text-[#cbd5e1]">' + htmlEscape(ev.note) + '</span>' : '';
    var div = document.createElement('div');
    div.className = 'p-2 mono text-xs border border-[#1f2330] rounded ' + cls;
    div.innerHTML =
      '<span class="text-[#94a3b8]">' + htmlEscape(ev.ts) + '</span> ' +
      '<span class="text-[#e8e8f0] font-semibold">' + htmlEscape(ev.agent) + '</span> ' +
      '<span class="badge ' + cls.replace('row-', 'bg-') + '">' + htmlEscape(ev.action) + '</span>' +
      '<span class="text-[#6b7280] ml-2">stage=' + htmlEscape(ev.stage || '-') +
      ' status=' + htmlEscape(ev.status || '-') + '</span>' +
      fieldsHtml + noteHtml +
      (ev.video_id ? ' <span class="text-[#6b7280] ml-2">vid=' + htmlEscape(ev.video_id) + '</span>' : '');
    log.prepend(div);
    var container = log.parentElement;
    container.scrollTop = 0;
  }

  ws.onopen = function () { status.textContent = 'Conectado ao fluxo de eventos em tempo real.'; status.className = 'text-xs text-[#22c55e] mb-2 pulse'; };
  ws.onclose = function () { status.textContent = 'Desconectado do fluxo de eventos.'; status.className = 'text-xs text-[#ef4444] mb-2'; };
  ws.onerror = function () { status.textContent = 'Erro na conexão de eventos.'; status.className = 'text-xs text-[#ef4444] mb-2'; };
  ws.onmessage = function (evt) {
    try { append(JSON.parse(evt.data)); } catch (e) { console.error(e); }
  };

  // Carrega histórico inicial via REST.
  fetch('/api/agent-events?limit=50')
    .then(function (r) { return r.json(); })
    .then(function (data) {
      (data.events || []).slice().reverse().forEach(append);
      countEl.textContent = data.count || 0;
      lastSeqEl.textContent = data.last_seq || '-';
    })
    .catch(function () {});
})();
</script>
</body>
</html>""", media_type="text/html")

@app.get("/", dependencies=[Depends(verify_api_key)])
def root() -> dict[str, Any]:
    """Informações da API e links úteis."""
    return {
        "name": "TikTok AI Autopilot — UI ao Vivo",
        "version": "1.0.0",
        "endpoints": {
            "videos": "/api/videos",
            "video_detail": "/api/videos/{video_id}",
            "stats": "/api/stats",
            "health": "/api/health",
            "metrics": "/api/metrics",
            "websocket": "/api/ws",
        },
        "docs": "/docs",
    }


# ===========================================================================
# Endpoints POST — ações sobre vídeos
# ===========================================================================
@app.post("/api/videos", dependencies=[Depends(verify_api_key)])
def generate_video(req: GenerateRequest) -> dict[str, Any]:
    """Gera um novo vídeo delegando para orch.generate()."""
    orch = get_orchestrator()
    try:
        res = orch.generate(
            topic=req.topic,
            kind=req.kind,
            template=req.template,
            target_seconds=req.target_seconds,
            execution_id=req.execution_id,
        )
    except PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "execution_id": res.execution_id,
        "video_id": res.video_id,
        "stage": res.stage,
        "status": res.status,
        "changed": res.changed,
        "note": res.note,
    }


@app.post("/api/videos/{video_id}/approve", dependencies=[Depends(verify_api_key)])
def approve_video(video_id: str, req: ApproveRequest) -> dict[str, Any]:
    """Aprova um vídeo em AWAITING_APPROVAL (transição para APPROVED)."""
    orch = get_orchestrator()
    row = orch.db.get_video(video_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Vídeo não encontrado: {video_id}")
    if row["status"] != "AWAITING_APPROVAL":
        raise HTTPException(
            status_code=409,
            detail=f"Vídeo {video_id} não está em AWAITING_APPROVAL (atual: {row['status']})",
        )
    try:
        res = orch.approve(video_id, actor=req.actor)
    except PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Notificar WebSocket
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        loop.create_task(
            manager.send_status_changed(
                video_id, row["status"], res.status, req.actor, "aprovado via UI"
            )
        )
    return {
        "video_id": video_id,
        "from_status": row["status"],
        "to_status": res.status,
        "changed": res.changed,
        "note": res.note,
    }


@app.post("/api/videos/{video_id}/reject", dependencies=[Depends(verify_api_key)])
def reject_video(video_id: str, req: RejectRequest) -> dict[str, Any]:
    """Reprova um vídeo, transicionando para FAILED com motivo informado."""
    orch = get_orchestrator()
    row = orch.db.get_video(video_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Vídeo não encontrado: {video_id}")
    allowed = ALLOWED_TRANSITIONS.get(row["status"], frozenset())
    if "FAILED" not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Não é possível rejeitar {video_id} do estado {row['status']}",
        )
    try:
        orch.db.record_error(
            video_id, req.motivo, to_status="FAILED", actor=req.actor
        )
    except PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        loop.create_task(
            manager.send_status_changed(
                video_id, row["status"], "FAILED", req.actor, f"rejeitado: {req.motivo}"
            )
        )
    return {
        "video_id": video_id,
        "from_status": row["status"],
        "to_status": "FAILED",
        "motivo": req.motivo,
    }


@app.post("/api/videos/{video_id}/retry", dependencies=[Depends(verify_api_key)])
def retry_video(video_id: str) -> dict[str, Any]:
    """Retenta um vídeo em FAILED (limpa erro e prepara para retomada)."""
    orch = get_orchestrator()
    row = orch.db.get_video(video_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Vídeo não encontrado: {video_id}")
    if row["status"] != "FAILED":
        raise HTTPException(
            status_code=409,
            detail=f"Vídeo {video_id} não está em FAILED (atual: {row['status']})",
        )
    try:
        orch.db.clear_error(video_id)
    except PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "video_id": video_id,
        "from_status": "FAILED",
        "to_status": row["status"],  # permanece no estado anterior ao erro
        "note": "erro limpo, pronto para retomada",
    }


@app.post("/api/advance/{video_id}/{stage}", dependencies=[Depends(verify_api_key)])
def advance_stage(video_id: str, stage: str, req: AdvanceRequest) -> dict[str, Any]:
    """Avança um estágio específico do pipeline (script, tts, render, validate, upload, publish)."""
    if stage not in STAGE_ORDER:
        raise HTTPException(status_code=400, detail=f"Estágio desconhecido: {stage}")
    orch = get_orchestrator()
    row = orch.db.get_video(video_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Vídeo não encontrado: {video_id}")
    current = row["status"]
    try:
        res = orch.run_stage(video_id, stage, actor=req.actor)
    except PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Notificar WebSocket se houve mudança de estado
    if res.changed and res.status != current:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(
                manager.send_status_changed(
                    video_id, current, res.status, req.actor, res.note
                )
            )
    return {
        "video_id": video_id,
        "stage": stage,
        "from_status": current,
        "to_status": res.status,
        "changed": res.changed,
        "note": res.note,
    }


__all__ = ["app", "manager", "get_orchestrator"]
