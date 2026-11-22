"""Banco de dados SQLite para controle de vídeos, estados e agendamentos.

Tabelas:
- videos: registro principal de cada vídeo (id, tema, roteiro, caminhos, status, ...)
- video_history: histórico de mudanças de estado (auditoria)
- scheduled_posts: posts agendados no Buffer (id remoto, estado, horário, ...)
- execution_runs: identificador único de cada execução do pipeline (retomada segura)
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from .errors import DuplicateError, PipelineError

# Estados válidos do pipeline, em ordem de progresso.
VIDEO_STATES: frozenset[str] = frozenset(
    {
        "IDEA_CREATED",
        "SCRIPT_READY",
        "RENDERING",
        "RENDERED",
        "VALIDATED",
        "AWAITING_APPROVAL",
        "APPROVED",
        "UPLOADING",
        "UPLOADED",
        "QUEUED",
        "PUBLISHING",
        "PUBLISHED",
        "ACTION_REQUIRED",
        "FAILED",
    }
)

# Transições permitidas: origem -> conjunto de destinos possíveis.
ALLOWED_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "IDEA_CREATED": {"SCRIPT_READY", "FAILED"},
    "SCRIPT_READY": {"RENDERING", "FAILED"},
    "RENDERING": {"RENDERED", "FAILED"},
    "RENDERED": {"VALIDATED", "FAILED"},
    "VALIDATED": {"AWAITING_APPROVAL", "FAILED"},
    "AWAITING_APPROVAL": {"APPROVED", "FAILED"},
    "APPROVED": {"UPLOADING", "FAILED"},
    "UPLOADING": {"UPLOADED", "FAILED"},
    "UPLOADED": {"QUEUED", "FAILED"},
    "QUEUED": {"PUBLISHING", "FAILED"},
    "PUBLISHING": {"PUBLISHED", "ACTION_REQUIRED", "FAILED"},
    "PUBLISHED": frozenset(),  # terminal
    "ACTION_REQUIRED": {"PUBLISHING", "FAILED"},
    "FAILED": {"RENDERING", "UPLOADING", "QUEUED", "PUBLISHING"},  # retomável
}

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "videos.sqlite3"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_load(text: str | None) -> Any:
    if text is None or text == "":
        return None
    return json.loads(text)


class Database:
    """Wrapper SQLite com transações, estados validados e idempotência."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.transaction() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS videos (
                    video_id           TEXT PRIMARY KEY,
                    execution_id       TEXT NOT NULL,
                    topic              TEXT NOT NULL,
                    template           TEXT NOT NULL,
                    kind               TEXT NOT NULL,
                    title              TEXT NOT NULL,
                    description        TEXT NOT NULL,
                    hashtags           TEXT NOT NULL,   -- JSON array
                    script_json        TEXT NOT NULL,   -- JSON do roteiro completo
                    total_seconds      REAL NOT NULL,
                    sources_json       TEXT,            -- JSON array de fontes
                    mp4_path           TEXT,
                    mp4_hash           TEXT,
                    duration_seconds   REAL,
                    width              INTEGER,
                    height             INTEGER,
                    fps                INTEGER,
                    audio_present      INTEGER DEFAULT 0,
                    captions_present   INTEGER DEFAULT 0,
                    status             TEXT NOT NULL DEFAULT 'IDEA_CREATED',
                    scheduled_at       TEXT,            -- ISO 8601 UTC
                    cloudinary_id      TEXT,
                    cloudinary_url     TEXT,
                    buffer_post_id     TEXT,
                    buffer_post_state  TEXT,
                    error_message      TEXT,
                    retry_count        INTEGER DEFAULT 0,
                    created_at         TEXT NOT NULL,
                    updated_at         TEXT NOT NULL,
                    CHECK (status IN (
                        'IDEA_CREATED','SCRIPT_READY','RENDERING','RENDERED',
                        'VALIDATED','AWAITING_APPROVAL','APPROVED','UPLOADING',
                        'UPLOADED','QUEUED','PUBLISHING','PUBLISHED',
                        'ACTION_REQUIRED','FAILED'
                    ))
                );
                CREATE TABLE IF NOT EXISTS video_history (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id     TEXT NOT NULL,
                    from_status  TEXT,
                    to_status    TEXT NOT NULL,
                    actor        TEXT NOT NULL,
                    note         TEXT,
                    created_at   TEXT NOT NULL,
                    FOREIGN KEY (video_id) REFERENCES videos(video_id)
                );
                CREATE TABLE IF NOT EXISTS scheduled_posts (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id       TEXT NOT NULL,
                    buffer_post_id TEXT NOT NULL,
                    state          TEXT NOT NULL,
                    scheduled_at   TEXT,
                    platform       TEXT NOT NULL DEFAULT 'tiktok',
                    created_at     TEXT NOT NULL,
                    updated_at     TEXT NOT NULL,
                    FOREIGN KEY (video_id) REFERENCES videos(video_id)
                );
                CREATE TABLE IF NOT EXISTS execution_runs (
                    execution_id  TEXT PRIMARY KEY,
                    started_at    TEXT NOT NULL,
                    finished_at   TEXT,
                    status        TEXT NOT NULL,  -- RUNNING, FINISHED, FAILED
                    error_message TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
                CREATE INDEX IF NOT EXISTS idx_videos_execution ON videos(execution_id);
                CREATE INDEX IF NOT EXISTS idx_history_video ON video_history(video_id);
                CREATE INDEX IF NOT EXISTS idx_scheduled_video ON scheduled_posts(video_id);
                """
            )

    # ------------------------------------------------------------------ vídeo
    def create_video(
        self,
        *,
        video_id: str,
        execution_id: str,
        topic: str,
        template: str,
        kind: str,
        title: str,
        description: str,
        hashtags: Sequence[str],
        script_json: Mapping[str, Any],
        total_seconds: float,
        sources_json: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> None:
        """Insere um novo vídeo em IDEA_CREATED. Idempotente por video_id."""
        now = _utc_now().isoformat()
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT video_id FROM videos WHERE video_id = ?", (video_id,)
            ).fetchone()
            if existing is not None:
                raise DuplicateError(f"Vídeo já registrado: {video_id}")
            conn.execute(
                """
                INSERT INTO videos (
                    video_id, execution_id, topic, template, kind, title, description,
                    hashtags, script_json, total_seconds, sources_json, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'IDEA_CREATED', ?, ?)
                """,
                (
                    video_id,
                    execution_id,
                    topic,
                    template,
                    kind,
                    title,
                    description,
                    _json_dump(list(hashtags)),
                    _json_dump(script_json),
                    float(total_seconds),
                    _json_dump(sources_json),
                    now,
                    now,
                ),
            )
            self._append_history(conn, video_id, None, "IDEA_CREATED", "system", "criado")

    def get_video(self, video_id: str) -> Optional[sqlite3.Row]:
        with self.transaction() as conn:
            return conn.execute(
                "SELECT * FROM videos WHERE video_id = ?", (video_id,)
            ).fetchone()

    def list_by_status(self, status: str) -> list[sqlite3.Row]:
        with self.transaction() as conn:
            return conn.execute(
                "SELECT * FROM videos WHERE status = ? ORDER BY created_at",
                (status,),
            ).fetchall()

    def transition(
        self,
        video_id: str,
        to_status: str,
        *,
        actor: str = "system",
        note: Optional[str] = None,
        **fields: Any,
    ) -> None:
        """Transaciona o estado de um vídeo com validação e campos opcionais.

        Idempotente: se o vídeo já estiver em ``to_status`` e não houver campos
        para atualizar, não grava histórico duplicado.
        """
        if to_status not in VIDEO_STATES:
            raise PipelineError(f"Estado inválido: {to_status}")

        now = _utc_now().isoformat()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT status FROM videos WHERE video_id = ?", (video_id,)
            ).fetchone()
            if row is None:
                raise PipelineError(f"Vídeo não encontrado: {video_id}")

            from_status = row["status"]
            if from_status != to_status:
                allowed = ALLOWED_TRANSITIONS.get(from_status, frozenset())
                if to_status not in allowed:
                    raise PipelineError(
                        f"Transição não permitida: {from_status} -> {to_status}"
                    )

            sets = ", ".join(f"{k} = ?" for k in fields)
            sql = f"UPDATE videos SET status = ?, updated_at = ?{', ' + sets if sets else ''} WHERE video_id = ?"
            params: list[Any] = [to_status, now]
            for v in fields.values():
                if isinstance(v, (dict, list, tuple)):
                    params.append(_json_dump(v))
                else:
                    params.append(v)
            params.append(video_id)
            conn.execute(sql, params)

            if to_status != from_status or note:
                self._append_history(
                    conn, video_id, from_status, to_status, actor, note or ""
                )

    def record_error(
        self,
        video_id: str,
        error_message: str,
        *,
        to_status: str = "FAILED",
        actor: str = "system",
    ) -> None:
        """Marca falha, incrementa retry_count e registra erro."""
        now = _utc_now().isoformat()
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT status, retry_count FROM videos WHERE video_id = ?",
                (video_id,),
            ).fetchone()
            if row is None:
                raise PipelineError(f"Vídeo não encontrado: {video_id}")
            from_status = row["status"]
            allowed = ALLOWED_TRANSITIONS.get(from_status, frozenset())
            if to_status not in allowed:
                raise PipelineError(
                    f"Transição de erro não permitida: {from_status} -> {to_status}"
                )
            retry_count = int(row["retry_count"]) + 1
            conn.execute(
                """
                UPDATE videos
                SET status = ?, error_message = ?, retry_count = ?, updated_at = ?
                WHERE video_id = ?
                """,
                (to_status, error_message, retry_count, now, video_id),
            )
            self._append_history(
                conn,
                video_id,
                row["status"],
                to_status,
                actor,
                f"erro: {error_message} (tentativa {retry_count})",
            )

    def clear_error(self, video_id: str) -> None:
        """Remove mensagem de erro (usado antes de retomar)."""
        with self.transaction() as conn:
            conn.execute(
                "UPDATE videos SET error_message = NULL, updated_at = ? WHERE video_id = ?",
                (_utc_now().isoformat(), video_id),
            )

    def _append_history(
        self,
        conn: sqlite3.Connection,
        video_id: str,
        from_status: Optional[str],
        to_status: str,
        actor: str,
        note: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO video_history (video_id, from_status, to_status, actor, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (video_id, from_status, to_status, actor, note, _utc_now().isoformat()),
        )

    # ------------------------------------------------------------------ posts
    def upsert_scheduled_post(
        self,
        *,
        video_id: str,
        buffer_post_id: str,
        state: str,
        scheduled_at: Optional[str] = None,
        platform: str = "tiktok",
    ) -> None:
        """Insere ou atualiza o registro de um post no Buffer (idempotente)."""
        now = _utc_now().isoformat()
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT id FROM scheduled_posts WHERE buffer_post_id = ?",
                (buffer_post_id,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO scheduled_posts
                        (video_id, buffer_post_id, state, scheduled_at, platform, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        video_id,
                        buffer_post_id,
                        state,
                        scheduled_at,
                        platform,
                        now,
                        now,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE scheduled_posts
                    SET state = ?, scheduled_at = ?, updated_at = ?
                    WHERE buffer_post_id = ?
                    """,
                    (state, scheduled_at, now, buffer_post_id),
                )

    def get_scheduled_post(self, buffer_post_id: str) -> Optional[sqlite3.Row]:
        with self.transaction() as conn:
            return conn.execute(
                "SELECT * FROM scheduled_posts WHERE buffer_post_id = ?",
                (buffer_post_id,),
            ).fetchone()

    def get_scheduled_post_by_video(self, video_id: str) -> Optional[sqlite3.Row]:
        """Devolve o post mais recente já gravado para um vídeo (idempotência)."""
        with self.transaction() as conn:
            return conn.execute(
                """
                SELECT * FROM scheduled_posts WHERE video_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (video_id,),
            ).fetchone()

    # ------------------------------------------------------------------ execução
    def start_execution(self, execution_id: str) -> None:
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT execution_id FROM execution_runs WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if existing is not None:
                raise DuplicateError(f"Execução já registrada: {execution_id}")
            conn.execute(
                "INSERT INTO execution_runs (execution_id, started_at, status) VALUES (?, ?, 'RUNNING')",
                (execution_id, _utc_now().isoformat()),
            )

    def finish_execution(self, execution_id: str, *, status: str, error_message: Optional[str] = None) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE execution_runs SET finished_at = ?, status = ?, error_message = ? WHERE execution_id = ?",
                (_utc_now().isoformat(), status, error_message, execution_id),
            )

    def close(self) -> None:
        # Nenhuma conexão persistente; mantido para interface compatível.
        return None


__all__ = [
    "VIDEO_STATES",
    "ALLOWED_TRANSITIONS",
    "Database",
    "DEFAULT_DB_PATH",
]
