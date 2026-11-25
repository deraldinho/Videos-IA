"""Orquestrador baseado no grafo LangGraph (CP05–CP29).

Conecta o grafo ``build_graph`` aos entry points operacionais (CLI e API)
de forma que o pipeline rode através dos agentes especializados
(research → idea → factcheck → script → voice → visual → render →
transcription → quality → compliance → human_approval → publishing →
monitoring → analytics → audit).

Responsabilidades:
- Cria/retoma vídeos no banco SQLite, sincronizando status com o grafo.
- Invoca o grafo com checkpointer persistente (SqliteSaver) para retomada segura.
- Trata a interrupção de aprovação humana (``interrupt``) e a retomada via
  ``Command(resume=...)``.
- Emite eventos no ``event_bus`` (CP29) — started/completed/error por nó do
  grafo (já via ``_instrumentado``) e transições de estado no banco.
- Mantém compatível com os entry points da CLI (``generate``, ``run_stage``,
  ``status`` etc.).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from src.contracts import DB_STATE_ORDER
from src.database import ALLOWED_TRANSITIONS, Database, VIDEO_STATES
from src.errors import DuplicateError, PipelineError
from src.settings import Settings, load_settings
from src.workflow.events import (
    ACTION_COMPLETED,
    ACTION_ERROR,
    ACTION_STARTED,
    ACTION_TRANSITION,
    event_bus,
    emit,
)
from src.workflow.graph import build_graph, new_checkpointer
from src.workflow.state import PublicationStatus, Stage
from src.orchestrator import ExecutionResult


_GRAPH_TO_DB_STATUS: dict[str, str] = {
    "IDEA_RESEARCHED": Stage.IDEA_CREATED.value,
    "IDEA_READY": Stage.SCRIPT_READY.value,
    "FACTCHECKED": Stage.SCRIPT_READY.value,
    "VOICE_READY": Stage.SCRIPT_READY.value,
    "VISUAL_READY": Stage.RENDERING.value,
    "TRANSCRIBED": Stage.RENDERED.value,
    "COMPLIANCE_CHECKED": Stage.AWAITING_APPROVAL.value,
    "UPLOADING": Stage.APPROVED.value,
    "UPLOADED": Stage.APPROVED.value,
    "QUEUED": Stage.QUEUED.value,
}

# Ordem linear dos estados do banco (para reconstruir caminhos válidos quando o
# grafo salta estados intermediários).
# Importado de src.contracts (fonte única de verdade).
_DB_STATE_ORDER = DB_STATE_ORDER


@dataclass(frozen=True)
class GraphExecutionResult:
    execution_id: str
    video_id: str
    stage: str
    status: str
    changed: bool
    note: str
    interrupted: bool = False


class GraphOrchestrator:
    """Executa o pipeline através do grafo LangGraph com checkpoints persistente."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        db_path: Path | str | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.db = Database(db_path) if db_path else Database()
        # CR-08 / CGR-006: o checkpointer do grafo LangGraph deve ser isolado
        # por ambiente (db_path), nunca hardcoded em data/langgraph_checkpoints.sqlite3,
        # que compartilharia estado entre ambientes (dev/test/production) e
        # entre worktrees paralelos. Deriva o caminho do checkpointer a partir
        # do db_path informado, mantendo o default apenas quando nenhum db_path
        # for fornecido (compatibilidade com comportamento anterior).
        if db_path is not None:
            db_path_obj = Path(db_path)
            self._checkpoint_db = str(
                db_path_obj.with_name(f"{db_path_obj.stem}_checkpoints.sqlite3")
            )
        else:
            self._checkpoint_db = str(Path("data") / "langgraph_checkpoints.sqlite3")

    # ------------------------------------------------------------------ #
    # Helpers de estado <-> banco                                         #
    # ------------------------------------------------------------------ #
    def _config(self, execution_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": execution_id}}

    @staticmethod
    def _json_load(text: Any) -> Any:
        if text is None or text == "":
            return None
        if isinstance(text, (list, dict)):
            return text
        return json.loads(text)

    @staticmethod
    def _ensure_state(state: dict[str, Any]) -> dict[str, Any]:
        if not state.get("execution_id"):
            state["execution_id"] = f"exec-{uuid.uuid4().hex[:12]}"
        if not state.get("video_id"):
            state["video_id"] = state["execution_id"]
        if not state.get("workflow_version"):
            state["workflow_version"] = "1.0.0"
        if not state.get("status"):
            state["status"] = Stage.IDEA_CREATED.value
        if not state.get("current_stage"):
            state["current_stage"] = Stage.IDEA_CREATED.value
        return state

    def _db_to_graph_state(self, row: Any, *, topic: Optional[str] = None,
                           kind: Optional[str] = None) -> dict[str, Any]:
        """Monta o estado inicial do grafo a partir da linha do banco."""
        state: dict[str, Any] = {
            "execution_id": row["execution_id"],
            "video_id": row["video_id"],
            "topic": topic or row["topic"],
            "kind": kind or row["kind"] or row["topic"],
            "template": row["template"],
            "title": row["title"],
            "description": row["description"],
            "hashtags": self._json_load(row["hashtags"]) or [],
            "script": self._json_load(row["script_json"]) or {},
            "total_seconds": float(row["total_seconds"] or 0.0),
            "mode": self.settings.mode,
            "live_enabled": bool(self.settings.live_enabled),
            "auto_enabled": bool(self.settings.auto_enabled),
            "status": row["status"],
            "current_stage": row["status"],
            "started_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

        sources = self._json_load(row["sources_json"])
        if sources:
            state["research_sources"] = sources

        if row["mp4_path"]:
            state["video_assets"] = [
                {
                    "mp4_path": row["mp4_path"],
                    "duration_seconds": row["duration_seconds"],
                    "width": row["width"],
                    "height": row["height"],
                    "fps": row["fps"],
                    "has_audio": bool(row["audio_present"]),
                }
            ]

        state["cloudinary_url"] = row["cloudinary_url"]
        state["cloudinary_id"] = row["cloudinary_id"]
        state["buffer_post_id"] = row["buffer_post_id"]
        state["buffer_post_state"] = row["buffer_post_state"]
        state["publication_status"] = (
            row["buffer_post_state"] if row["buffer_post_state"] else "none"
        )
        state["error_message"] = row["error_message"]
        state["retries"] = int(row["retry_count"] or 0)
        return self._ensure_state(state)

    def _graph_state_to_db(self, video_id: str, state: dict[str, Any]) -> None:
        """Persiste o status final do grafo no banco (com mapeamento seguro).

        O grafo pode pular estados intermediários (ex.: ``AWAITING_APPROVAL ->
        APPROVED -> UPLOADING`` após a retomada de uma aprovação humana), e o
        banco só aceita transições adjacentes em ``ALLOWED_TRANSITIONS``. Por
        isso, aplicamos a transição estado a estado até atingir o destino
        final, ignorando estados que já são o destino ou que o grafo reportou
        como concluídos.
        """
        status = state.get("status", Stage.IDEA_CREATED.value)
        # NUNCA default para PUBLISHED: apenas mapeamentos explicitos sao
        # permitidos. Qualquer status do grafo que nao corresponda a um estado
        # do banco (ex.: MONITORED, TARGETS_PREPARADO, ANALYTICAS) nao
        # resulta em gravacao de status; apenas campos adicionais sao persistidos.
        if status in VIDEO_STATES:
            db_status = status
        else:
            db_status = _GRAPH_TO_DB_STATUS.get(status)

        fields: dict[str, Any] = {}
        script = state.get("script")
        if script is not None:
            fields["script_json"] = json.dumps(script, ensure_ascii=False)
        if state.get("total_seconds") is not None:
            fields["total_seconds"] = float(state["total_seconds"])

        video_assets = state.get("video_assets") or []
        if isinstance(video_assets, list) and video_assets:
            last_asset = video_assets[-1]
            fields["mp4_path"] = last_asset.get("mp4_path")
            fields["duration_seconds"] = last_asset.get("duration_seconds")
            fields["width"] = last_asset.get("width")
            fields["height"] = last_asset.get("height")
            fps = last_asset.get("fps")
            fields["fps"] = int(fps) if fps is not None else None
            fields["audio_present"] = 1 if last_asset.get("has_audio") else 0

        if state.get("cloudinary_url"):
            fields["cloudinary_url"] = state["cloudinary_url"]
        if state.get("cloudinary_id"):
            fields["cloudinary_id"] = state["cloudinary_id"]
        if state.get("buffer_post_id"):
            fields["buffer_post_id"] = state["buffer_post_id"]
        if state.get("buffer_post_state"):
            fields["buffer_post_state"] = state["buffer_post_state"]

        note = state.get("note", "")

        row = self.db.get_video(video_id)
        if row is None:
            return

        current = row["status"]
        if db_status is None:
            # Status do grafo nao mapeia para estado do banco (ex.: MONITORED,
            # TARGETS_PREPARADO, ANALYTICAS): nao altera o status do video,
            # apenas persiste campos adicionais se houver.
            if fields:
                self.db.transition(
                    video_id, current, actor="system", note=note, **fields
                )
            return
        if current == db_status:
            # Já está no destino: apenas grava campos adicionais se houver.
            if fields:
                self.db.transition(
                    video_id, current, actor="system", note=note, **fields
                )
            return

        # Apenas transicoes validas e diretas (adjacentes) a partir do estado
        # atual sao aplicadas. Nao recriamos caminhos artificiais (BFS) que
        # pulem estados intermediarios nunca percorridos pelo grafo, pois isso
        # gravaria historico de estados que o video nunca realmente atingiu
        # (Finding CR-02: transicoes artificiais no SQLite).
        allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
        if db_status not in allowed:
            # Destino nao e uma transicao direta valida a partir de current:
            # nao altera o status, apenas persiste campos adicionais se houver.
            if fields:
                self.db.transition(
                    video_id, current, actor="system", note=note, **fields
                )
            return

        # Transicao direta valida: aplica uma etapa unica, respeitando o state
        # machine do banco (sem estados artificiais intermediarios).
        self.db.transition(
            video_id, db_status, actor="system", note=note, **fields
        )

    # ------------------------------------------------------------------ #
    # Invocação do grafo com checkpointer ativo                           #
    # ------------------------------------------------------------------ #
    def _invoke_graph(self, execution_id: str, state: dict[str, Any]) -> dict[str, Any]:
        """Executa o grafo com um SqliteSaver ativo (persistente por thread_id)."""
        checkpointer = new_checkpointer(self._checkpoint_db)
        with checkpointer as cp:
            graph = build_graph(checkpointer=cp)
            config = self._config(execution_id)
            return graph.invoke(state, config=config)

    # ------------------------------------------------------------------ #
    # Entry points compatíveis com a CLI                                 #
    # ------------------------------------------------------------------ #
    def generate(
        self,
        *,
        topic: Optional[str] = None,
        kind: Optional[str] = None,
        template: Optional[str] = None,
        target_seconds: Optional[int] = None,
        execution_id: Optional[str] = None,
    ) -> GraphExecutionResult:
        """Cria um vídeo (IDEA_CREATED) e roda o grafo até roteiro pronto
        (ou interrupção de aprovação humana / fim do fluxo)."""
        execution_id = execution_id or f"exec-{uuid.uuid4().hex[:12]}"
        kind = kind or self.settings.script_kind
        template = template or "curiosidade"
        target_seconds = target_seconds or self.settings.video.target_seconds

        try:
            self.db.start_execution(execution_id)
        except DuplicateError:
            pass  # retomada idempotente

        try:
            self.db.create_video(
                video_id=execution_id,
                execution_id=execution_id,
                topic=topic or "a definir",
                template=template,
                kind=kind,
                title="a definir",
                description="a definir",
                hashtags=[],
                script_json={},
                total_seconds=0.0,
            )
        except DuplicateError:
            pass  # já existe

        row = self.db.get_video(execution_id)
        if row is None:
            raise PipelineError(f"Não foi possível criar o vídeo {execution_id}")

        state = self._db_to_graph_state(row, topic=topic, kind=kind)

        emit(
            "graph_orchestrator",
            ACTION_STARTED,
            state,
            note="generate iniciado via grafo LangGraph",
            stage="generate",
        )

        result_state = self._invoke_graph(execution_id, state)
        interrupted = "__interrupt__" in result_state

        if interrupted:
            self._graph_state_to_db(execution_id, result_state)
            return GraphExecutionResult(
                execution_id=execution_id,
                video_id=execution_id,
                stage="script",
                status="AWAITING_APPROVAL",
                changed=True,
                note="roteiro gerado; aguardando aprovação humana (interrupção do grafo)",
                interrupted=True,
            )

        self._graph_state_to_db(execution_id, result_state)
        final_status = result_state.get("status", Stage.IDEA_CREATED.value)
        emit(
            "graph_orchestrator",
            ACTION_COMPLETED,
            result_state,
            note="generate concluído via grafo",
            stage="generate",
            status=final_status,
        )
        return GraphExecutionResult(
            execution_id=execution_id,
            video_id=execution_id,
            stage=result_state.get("current_stage", "script"),
            status=final_status,
            changed=True,
            note=result_state.get("note", "pipeline executado via grafo LangGraph"),
            interrupted=False,
        )

    def run_pipeline(self, video_id: str, *, actor: str = "system") -> GraphExecutionResult:
        """Roda o grafo a partir do estado atual do vídeo no banco."""
        row = self.db.get_video(video_id)
        if row is None:
            raise PipelineError(f"Vídeo não encontrado: {video_id}")

        state = self._db_to_graph_state(row)
        emit(
            "graph_orchestrator",
            ACTION_STARTED,
            state,
            note="run_pipeline iniciado via grafo",
            stage=row["status"],
        )

        result_state = self._invoke_graph(row["execution_id"], state)
        interrupted = "__interrupt__" in result_state

        self._graph_state_to_db(video_id, result_state)
        if interrupted:
            return GraphExecutionResult(
                execution_id=row["execution_id"],
                video_id=video_id,
                stage=result_state.get("current_stage", row["status"]),
                status=result_state.get("status", row["status"]),
                changed=True,
                note="interrompido para aprovação humana",
                interrupted=True,
            )

        final_status = result_state.get("status", row["status"])
        return GraphExecutionResult(
            execution_id=row["execution_id"],
            video_id=video_id,
            stage=result_state.get("current_stage", row["status"]),
            status=final_status,
            changed=final_status != row["status"],
            note=result_state.get("note", "pipeline executado via grafo LangGraph"),
            interrupted=False,
        )

    def run_stage(self, video_id: str, stage: str,
                  *, actor: str = "system") -> GraphExecutionResult:
        """Executa o grafo a partir do estado atual do vídeo.

        O parâmetro ``stage`` é mantido por compatibilidade com a CLI/API
        existentes, mas o roteamento é feito pelo próprio grafo LangGraph
        (cada chamada avança o pipeline o quanto for possível a partir do
        estado persistido no banco).
        """
        return self.run_pipeline(video_id, actor=actor)

    def resume_after_approval(
        self,
        video_id: str,
        *,
        decision: str,
        actor: str = "operador",
    ) -> GraphExecutionResult:
        """Retoma o grafo após aprovação/rejeição humana (Command(resume=...))."""
        row = self.db.get_video(video_id)
        if row is None:
            raise PipelineError(f"Vídeo não encontrado: {video_id}")

        if decision not in {"approved", "rejected"}:
            raise PipelineError(f"Decisão inválida: {decision} (use 'approved' ou 'rejected')")

        # Persiste a decisão humana no banco com uma transição adjacente válida
        # (AWAITING_APPROVAL -> APPROVED ou -> ACTION_REQUIRED). Assim, ao
        # chamar _graph_state_to_db após o grafo, o estado atual do banco já
        # reflete a decisão humana e apenas etapas subsequentes adjacentes
        # (ex.: APPROVED -> UPLOADING) são aplicadas, evitando históricos
        # artificiais (Finding CR-02).
        if decision == "approved":
            self.db.transition(
                video_id, "APPROVED", actor=actor,
                note="aprovação humana: aprovado",
            )
        else:
            self.db.transition(
                video_id, "ACTION_REQUIRED", actor=actor,
                note="aprovação humana: rejeitado",
            )

        checkpointer = new_checkpointer(self._checkpoint_db)
        with checkpointer as cp:
            graph = build_graph(checkpointer=cp)
            config = self._config(row["execution_id"])
            final = graph.invoke(Command(resume=decision), config=config)
        interrupted = "__interrupt__" in final

        self._graph_state_to_db(video_id, final)
        final_status = final.get("status", row["status"])
        emit(
            "graph_orchestrator",
            ACTION_TRANSITION,
            final,
            stage=final.get("current_stage"),
            status=final_status,
            note=f"aprovação humana: {decision}",
            fields={"decision": decision},
        )

        return GraphExecutionResult(
            execution_id=row["execution_id"],
            video_id=video_id,
            stage=final.get("current_stage", row["status"]),
            status=final_status,
            changed=final_status != row["status"],
            note=f"aprovação {decision} processada via grafo",
            interrupted=interrupted,
        )

    def approve(self, video_id: str, *, actor: str = "operador") -> GraphExecutionResult:
        """Aprova um vídeo e retoma o grafo (equivalente a approve via grafo)."""
        return self.resume_after_approval(video_id, decision="approved", actor=actor)

    def reject(
        self,
        video_id: str,
        *,
        actor: str = "operador",
        motivo: Optional[str] = None,
    ) -> GraphExecutionResult:
        """Rejeita um vídeo e retoma o grafo."""
        note = motivo or "rejeitado via CLI"
        return self.resume_after_approval(video_id, decision="rejected", actor=actor)

    def status(self, video_id: str) -> Optional[dict[str, Any]]:
        row = self.db.get_video(video_id)
        if not row:
            return None
        return dict(row)

    def list_by_status(self, status: str):
        return self.db.list_by_status(status)

    def close(self) -> None:
        self.db.close()


__all__ = [
    "GraphOrchestrator",
    "GraphExecutionResult",
    "build_graph",
    "new_checkpointer",
]
