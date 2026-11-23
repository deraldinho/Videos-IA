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

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from src.database import ALLOWED_TRANSITIONS, Database, VIDEO_STATES
from src.errors import PipelineError
from src.health import run_health_checks
from src.metrics import metrics
from src.orchestrator import Orchestrator, STAGE_ORDER


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
_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
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
# Root
# ---------------------------------------------------------------------------
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
        res = orch.sm.advance(
            video_id, "APPROVED", actor=req.actor, note="aprovado via UI ao vivo"
        )
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
                video_id, row["status"], "APPROVED", req.actor, "aprovado via UI"
            )
        )
    return {
        "video_id": video_id,
        "from_status": row["status"],
        "to_status": "APPROVED",
        "changed": res.changed,
        "note": "aprovado via UI ao vivo",
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
