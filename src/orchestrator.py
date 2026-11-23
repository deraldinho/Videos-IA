"""Orquestrador do pipeline: execução etapa a etapa com ID de execução único.

Coordena o fluxo completo:
  IDEA_CREATED → roteiro → TTS → render MP4 → validação → aprovação → Cloudinary → Buffer

Cada etapa avança a máquina de estados apenas se a anterior tiver sucesso. Em caso
de falha, o vídeo vai para FAILED e pode ser retomado. A execução é idempotente:
reexecutar com o mesmo execution_id não duplica trabalho.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

import wave

from src.ai.provider import build_provider
from src.database import ALLOWED_TRANSITIONS, Database, VIDEO_STATES
from src.errors import DuplicateError, PipelineError
from src.integrations.buffer_client import BufferClient
from src.integrations.cloudinary_client import CloudinaryClient
from src.scripts import load_script, read_catalog
from src.settings import Settings, load_settings
from src.state_machine import StateMachine
from src.video.hyperframes_adapter import HyperFramesAdapter
from src.video.renderer import HTMLRenderer
from src.circuit_breaker import CircuitBreaker
from src.retry import retry_call
from src.video.tts import PiperTTS
from src.video.validator import FFprobeValidator

# Etapas do pipeline, em ordem.
STAGE_ORDER = [
    "script",
    "tts",
    "render",
    "validate",
    "approve",
    "upload",
    "publish",
]


@dataclass(frozen=True)
class ExecutionResult:
    """Resultado de uma execução do pipeline."""
    execution_id: str
    video_id: str
    stage: str
    status: str
    changed: bool
    note: str


class Orchestrator:
    """Executa o pipeline de um vídeo, etapa por etapa, com validação de estado."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        db_path: Path | str | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.db = Database(db_path) if db_path else Database()
        self.sm = StateMachine(self.db)

        # Clientes (mock quando não live).
        self.cloudinary = CloudinaryClient(
            cloud_name=self.settings.cloudinary.cloud_name,
            api_key=self.settings.cloudinary.api_key,
            api_secret=self.settings.cloudinary.api_secret,
            live=self.settings.live_enabled,
            max_video_mb=self.settings.cloudinary.max_video_mb,
            stop_usage_percent=self.settings.cloudinary.stop_usage_percent,
        )
        self.buffer = BufferClient(
            api_key=None,
            organization_id=None,
            channel_id=None,
            live=self.settings.live_enabled,
            db=self.db,
        )

        # Circuit breakers + retry para chamadas de serviços externos
        # (Cloudinary upload e Buffer create_post). Em modo não-live (mock)
        # as chamadas nunca falham, então o circuito nunca abre e o retry não
        # é acionado: comportamento preservado. Em modo live, falhas
        # transitórias são retryadas e, após o threshold, o circuito abre
        # protegendo contra cascata de falhas (CP11 componentes, CP21 integração).
        self._cloudinary_cb = CircuitBreaker(
            name="cloudinary", fail_threshold=3, recovery_timeout_seconds=30.0,
        )
        self._buffer_cb = CircuitBreaker(
            name="buffer", fail_threshold=3, recovery_timeout_seconds=30.0,
        )

        # HyperFramesAdapter com fallback automático para HTMLRenderer.
        self.hyperframes_adapter = HyperFramesAdapter(
            vendor_dir=Path('vendor/hyperframes'),
            node_bin=self.settings.video.node,
            ffmpeg_bin=self.settings.video.ffmpeg,
            output_dir="output/drafts",
            settings=self.settings,
        )
        self.renderer = HTMLRenderer(settings=self.settings)
        # Validador de MP4 com thresholds/strict derivados do settings (CP18)
        # e ffprobe_bin do settings.video (CP19).
        self.validator = FFprobeValidator(
            ffprobe_bin=self.settings.video.ffprobe,
            thresholds=self.settings.quality.to_thresholds(),
        )
        self.catalog = self._load_catalog()

    # ------------------------------------------------------------------ #
    # Resiliência: circuit breaker + retry para serviços externos        #
    # ------------------------------------------------------------------ #
    def _call_with_resilience(self, cb: CircuitBreaker, func, *args, **kwargs):
        """Executa ``func`` protegida por circuit breaker + retry com backoff.

        Em modo não-live (mock) as chamadas externas nunca falham, portanto o
        circuito nunca abre e o retry não é acionado: comportamento idêntico ao
        de antes. Em modo live, falhas transitórias (rede, timeout) são
        retryadas com backoff exponencial e, após o threshold de falhas, o
        circuito abre bloqueando novas chamadas até o tempo de recuperação.
        """
        return retry_call(
            lambda: cb.call(func, *args, **kwargs),
            max_attempts=3,
            base_delay_seconds=0.5,
            max_delay_seconds=5.0,
            retryable_exceptions=(Exception,),
        )

    # Cliente Piper TTS: usa o modelo do config.yaml quando disponível;
    # em REVIEW/DRY_RUN gera silêncio fallback para não bloquear o pipeline.
        self.tts = PiperTTS(
            piper_bin="piper",
            model_path=self.settings.video.piper_model,
            output_dir=Path("output/drafts") / "tts",
            require_real=False,
        )

    def _load_catalog(self):
        catalog_path = Path("config/sources.yaml")
        if catalog_path.exists():
            return read_catalog(catalog_path)
        return {}

    # ------------------------------------------------------------------ #
    # Entry points públicos                                              #
    # ------------------------------------------------------------------ #
    def generate(
        self,
        *,
        topic: Optional[str] = None,
        kind: Optional[str] = None,
        template: Optional[str] = None,
        target_seconds: Optional[int] = None,
        execution_id: Optional[str] = None,
    ) -> ExecutionResult:
        """Cria um novo vídeo em IDEA_CREATED e gera o roteiro (assisted ou NVIDIA)."""
        execution_id = execution_id or f"exec-{uuid.uuid4().hex[:12]}"
        kind = kind or self.settings.script_kind
        template = template or "curiosidade"
        target_seconds = target_seconds or self.settings.video.target_seconds
        min_s = self.settings.video.min_seconds
        max_s = self.settings.video.max_seconds

        try:
            self.db.start_execution(execution_id)
        except DuplicateError:
            pass  # execução já existe: retomar

        # Tenta criar o vídeo (idempotente por video_id = execution_id).
        video_id = execution_id
        try:
            self.db.create_video(
                video_id=video_id,
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

        # Gera o roteiro via LLMProvider.
        provider = build_provider(
            self.settings.llm.provider,
            api_key=self.settings.llm.nvidia_api_key,
            model=self.settings.llm.nvidia_model,
            inbox=Path("data/inbox"),
        )
        from src.ideas import build_prompt  # import local para evitar ciclo
        prompt = build_prompt(
            niche=self.settings.niche,
            kind=kind,
            target_seconds=target_seconds,
            min_seconds=min_s,
            max_seconds=max_s,
            catalog=self.catalog,
            previous_topics=self._previous_topics(),
        )
        try:
            script_data = provider.complete(prompt)
        except PipelineError:
            # Em modo assisted, o operador ainda não devolveu o JSON.
            tr = self.sm.advance(video_id, "SCRIPT_READY", actor="system",
                                 note="roteiro pendente (assisted) — aguardando JSON do operador")
            return ExecutionResult(
                execution_id=execution_id, video_id=video_id,
                stage="script", status="SCRIPT_READY", changed=tr.changed,
                note="roteiro pendente de revisão humana (modo assisted)",
            )

        # Valida e monta o Script.
        script = load_script(script_data, self.catalog, float(min_s), float(max_s))

        # Se já está em SCRIPT_READY com roteiro gravado, não re-transiciona
        # (idempotência da retomada assisted: o operador devolveu o JSON).
        row = self.db.get_video(video_id)
        current_status = row["status"]
        if current_status == "SCRIPT_READY" and row["script_json"]:
            return ExecutionResult(
                execution_id=execution_id, video_id=video_id,
                stage="script", status="SCRIPT_READY", changed=False,
                note="roteiro já gravado (retomada assisted idempotente)",
            )

        # Avança para SCRIPT_READY com o roteiro gravado.
        self.sm.advance(
            video_id, "SCRIPT_READY", actor="system",
            note="roteiro gerado e validado",
            topic=script.topic,
            title=script.title,
            description=script.description,
            hashtags=list(script.hashtags),
            script_json=script.as_dict(),
            total_seconds=script.total_seconds,
            sources_json=[s.__dict__ for s in script.sources],
        )
        return ExecutionResult(
            execution_id=execution_id, video_id=video_id,
            stage="script", status="SCRIPT_READY", changed=True,
            note=f"roteiro '{script.topic}' gerado ({script.total_seconds:.1f}s)",
        )

    def run_stage(self, video_id: str, stage: str,
                  *, actor: str = "system") -> ExecutionResult:
        """Executa uma única etapa para um vídeo existente."""
        if stage not in STAGE_ORDER:
            raise PipelineError(f"Etapa desconhecida: {stage}")
        row = self.db.get_video(video_id)
        if row is None:
            raise PipelineError(f"Vídeo não encontrado: {video_id}")

        current = row["status"]

        if stage == "script":
            return self._stage_script(video_id, current, actor)
        if stage == "tts":
            return self._stage_tts(video_id, current, actor)
        if stage == "render":
            return self._stage_render(video_id, current, actor)
        if stage == "validate":
            return self._stage_validate(video_id, current, actor)
        if stage == "approve":
            return self._stage_approve(video_id, current, actor)
        if stage == "upload":
            return self._stage_upload(video_id, current, actor)
        if stage == "publish":
            return self._stage_publish(video_id, current, actor)
        raise PipelineError(f"Etapa desconhecida: {stage}")

    def run_pipeline(self, video_id: str, *,
                     actor: str = "system",
                     up_to: Optional[str] = None) -> list[ExecutionResult]:
        """Executa todas as etapas aplicáveis a partir do estado atual."""
        from src.metrics import increment_stage_attempt, increment_stage_success
        results: list[ExecutionResult] = []
        row = self.db.get_video(video_id)
        if row is None:
            raise PipelineError(f"Vídeo não encontrado: {video_id}")

        for stage in STAGE_ORDER:
            if up_to and STAGE_ORDER.index(stage) > STAGE_ORDER.index(up_to):
                break
            # Registra tentativa de estágio.
            increment_stage_attempt(stage)
            # Pula etapas já concluídas (estado já avançou além delas).
            res = self.run_stage(video_id, stage, actor=actor)
            results.append(res)
            if res.status == "FAILED":
                break
            # Registra sucesso de estágio (não foi FAILED).
            increment_stage_success(stage)
        return results

    # ------------------------------------------------------------------ #
    # Etapas individuais                                                 #
    # ------------------------------------------------------------------ #
    def _stage_script(self, video_id: str, current: str, actor: str) -> ExecutionResult:
        if current in {"SCRIPT_READY", "RENDERING", "RENDERED", "VALIDATED",
                       "AWAITING_APPROVAL", "APPROVED"}:
            return ExecutionResult(video_id, current, "script", current, False,
                                   "roteiro já pronto")
        # O roteiro deve já ter sido gerado via generate().
        row = self.db.get_video(video_id)
        script_json = row["script_json"] or {}
        if not script_json:
            raise PipelineError(f"Vídeo {video_id} não tem roteiro. Rode 'generate' primeiro.")
        self.sm.advance(video_id, "SCRIPT_READY", actor=actor,
                        note="roteiro confirmado")
        return ExecutionResult(video_id, current, "script", "SCRIPT_READY", True,
                               "roteiro confirmado")

    def _stage_tts(self, video_id: str, current: str, actor: str) -> ExecutionResult:
        if current in {"RENDERING", "RENDERED", "VALIDATED", "AWAITING_APPROVAL",
                       "APPROVED", "UPLOADING", "UPLOADED", "QUEUED", "PUBLISHING",
                       "PUBLISHED"}:
            return ExecutionResult(video_id, current, "tts", current, False,
                                   "TTS já feito")
        if current != "SCRIPT_READY":
            raise PipelineError(f"TTS requer SCRIPT_READY (atual: {current})")
        row = self.db.get_video(video_id)
        script_json = json.loads(row["script_json"]) if row["script_json"] else {}
        scenes = script_json.get("scenes", [])
        if not scenes:
            raise PipelineError(f"Vídeo {video_id} não tem cenas para TTS.")

        segments = []
        for idx, scene in enumerate(scenes):
            role = scene.get("role", "corpo")
            phrases = scene.get("narration", [])
            segs = self.tts.synthesize_scene(role, phrases, idx, prefix=f"{video_id}")
            segments.extend(segs)

        total_audio = sum(s.duration_seconds for s in segments)
        self.sm.advance(video_id, "RENDERING", actor=actor,
                        note=f"TTS concluído: {len(segments)} segmentos, {total_audio:.1f}s de áudio")
        return ExecutionResult(video_id, current, "tts", "RENDERING", True,
                               f"TTS: {len(segments)} segmentos")

    def _stage_render(self, video_id: str, current: str, actor: str) -> ExecutionResult:
        if current in {"RENDERED", "VALIDATED", "AWAITING_APPROVAL", "APPROVED"}:
            return ExecutionResult(video_id, current, "render", current, False,
                                   "vídeo já renderizado")
        if current != "RENDERING":
            raise PipelineError(f"Render requer RENDERING (atual: {current})")
        row = self.db.get_video(video_id)
        script_json = json.loads(row["script_json"]) if row["script_json"] else {}
        topic = row["topic"]
        template = row["template"]
        duration = float(row["total_seconds"] or 15.0)

        context = {
            "video_id": video_id,
            "topic": topic,
            "template": template,
            "scenes": script_json.get("scenes", []),
            "title": row["title"],
            "description": row["description"],
            "hashtags": script_json.get("hashtags", []),
            "duration_seconds": duration,
        }

        # Constrói a narração (TTS) como um único WAV e a incorpora ao MP4.
        # Mesmo sem o Piper instalado, grava silêncio para manter o pipeline
        # rodando; a validação em modo strict rejeita áudio silencioso.
        audio_wav = self._synthesize_narration(video_id, script_json)

        output_name = f"{video_id}.mp4"
        mp4_path = self.hyperframes_adapter.render(
            template, context, duration_seconds=duration, output_name=output_name,
            audio_wav=audio_wav,
        )

        self.sm.advance(
            video_id, "RENDERED", actor=actor,
            note=f"MP4 renderizado: {mp4_path.name}",
            mp4_path=str(mp4_path),
        )
        return ExecutionResult(video_id, current, "render", "RENDERED", True,
                               f"MP4 renderizado: {mp4_path.name}")

    def _synthesize_narration(self, video_id: str,
                              script_json: Mapping[str, Any]) -> Path | None:
        """Sintetiza a narração completa em um único WAV (ou silêncio fallback).

        Lê todas as frases de narração das cenas e as concatena com pausas,
        produzindo um WAV que o renderer muxa no MP4 final. Retorna None quando
        não há frases para narrar.
        """
        scenes = script_json.get("scenes", [])
        phrases: list[str] = []
        for scene in scenes:
            phrases.extend(scene.get("narration", []))
        if not phrases:
            return None

        output_dir = Path("output/drafts") / "tts"
        output_dir.mkdir(parents=True, exist_ok=True)
        wav_path = output_dir / f"{video_id}_narracao.wav"

        sample_rate = 22050
        gap_frames = b"\x00\x00" * int(sample_rate * 0.3)  # 300 ms entre frases
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            for idx, phrase in enumerate(phrases):
                segments = self.tts.synthesize_scene("narracao", [phrase], idx,
                                                     prefix=video_id)
                if segments:
                    seg_path = segments[0].wav_path
                    with wave.open(str(seg_path), "rb") as sf:
                        wf.writeframes(sf.readframes(sf.getnframes()))
                    wf.writeframes(gap_frames)
        return wav_path

    def _stage_validate(self, video_id: str, current: str, actor: str) -> ExecutionResult:
        if current in {"VALIDATED", "AWAITING_APPROVAL", "APPROVED"}:
            return ExecutionResult(video_id, current, "validate", current, False,
                                   "já validado")
        if current != "RENDERED":
            raise PipelineError(f"Validação requer RENDERED (atual: {current})")
        row = self.db.get_video(video_id)
        mp4_path = row["mp4_path"]
        if not mp4_path:
            raise PipelineError(f"Vídeo {video_id} não tem caminho do MP4.")
        path = Path(mp4_path)

        info = self.validator.validate_tiktok(
            path,
            width=self.settings.video.width,
            height=self.settings.video.height,
            fps=self.settings.video.fps,
            max_duration_seconds=float(self.settings.video.max_seconds),
            require_audio=True,
            max_size_mb=float(self.settings.cloudinary.max_video_mb),
            strict=self.settings.quality.strict,
        )

        self.sm.advance(
            video_id, "VALIDATED", actor=actor,
            note=f"MP4 válido ({info.width}x{info.height}, {info.fps:.1f}fps, {info.duration_seconds:.1f}s)",
            duration_seconds=info.duration_seconds,
            width=info.width,
            height=info.height,
            fps=int(round(info.fps)),
            audio_present=1 if info.has_audio else 0,
            captions_present=1 if info.caption_streams > 0 else 0,
        )
        return ExecutionResult(video_id, current, "validate", "VALIDATED", True,
                               f"válido ({info.duration_seconds:.1f}s)")

    def _stage_approve(self, video_id: str, current: str, actor: str) -> ExecutionResult:
        if current == "APPROVED":
            return ExecutionResult(video_id, current, "approve", current, False,
                                   "já aprovado")
        if current != "AWAITING_APPROVAL":
            raise PipelineError(f"Aprovação requer AWAITING_APPROVAL (atual: {current})")
        if self.settings.mode == "AUTO":
            if not self.settings.auto_enabled:
                raise PipelineError("Modo AUTO requer auto_enabled: true.")
            self.sm.advance(video_id, "APPROVED", actor=actor,
                            note="aprovado para publicação (modo AUTO)")
            return ExecutionResult(video_id, current, "approve", "APPROVED", True,
                                   "aprovado")
        # Em modo REVIEW/DRY_RUN, a aprovação é humana via CLI (approve command).
        # A etapa apenas registra; o estado não muda sem aprovação humana.
        return ExecutionResult(video_id, current, "approve", current, False,
                               "aguardando aprovação humana (modo REVIEW/DRY_RUN)")

    def _stage_upload(self, video_id: str, current: str, actor: str) -> ExecutionResult:
        if current in {"UPLOADED", "QUEUED", "PUBLISHING", "PUBLISHED"}:
            return ExecutionResult(video_id, current, "upload", current, False,
                                   "já enviado ao Cloudinary")
        if current != "APPROVED":
            raise PipelineError(f"Upload requer APPROVED (atual: {current})")
        row = self.db.get_video(video_id)
        mp4_path = row["mp4_path"]
        if not mp4_path:
            raise PipelineError(f"Vídeo {video_id} não tem MP4 para upload.")
        path = Path(mp4_path)
        if not path.exists():
            raise PipelineError(f"Arquivo MP4 não encontrado: {path}")

        # Transição determinística: APPROVED -> UPLOADING -> UPLOADED.
        # Nunca salta direto de APPROVED para UPLOADED (protege contra upload
        # / publicação sem as etapas intermediárias validadas).
        if current == "APPROVED":
            self.sm.advance(video_id, "UPLOADING", actor=actor,
                            note="iniciando upload para o Cloudinary")

        result = self._call_with_resilience(
            self._cloudinary_cb, self.cloudinary.upload, video_id, path
        )
        self.sm.advance(
            video_id, "UPLOADED", actor=actor,
            note=f"enviado ao Cloudinary: {result.public_id}",
            cloudinary_id=result.public_id,
            cloudinary_url=result.secure_url,
        )
        return ExecutionResult(video_id, current, "upload", "UPLOADED", True,
                               f"Cloudinary: {result.public_id}")

    def _stage_publish(self, video_id: str, current: str, actor: str) -> ExecutionResult:
        # Orchestrator._stage_publish() connects UPLOADED → PUBLISHED/QUEUED → Orchestrator
        # Graph edge: UPLOADED state → publish stage → PUBLISHED/QUEUED terminal states
        if current in {"PUBLISHED", "ACTION_REQUIRED"}:
            return ExecutionResult(video_id, current, "publish", current, False,
                                   "já publicado ou requer ação")
        if current != "UPLOADED":
            raise PipelineError(f"Publicação requer UPLOADED (atual: {current})")
        row = self.db.get_video(video_id)
        script_json = row["script_json"] or {}
        caption = script_json.get("description", "") + " " + " ".join(
            script_json.get("hashtags", [])
        )
        video_url = row["cloudinary_url"]
        if not video_url:
            raise PipelineError(f"Vídeo {video_id} não tem URL do Cloudinary.")

        due_at = row["scheduled_at"]
        post = self._call_with_resilience(
            self._buffer_cb, self.buffer.create_post,
            video_id=video_id, text=caption,
            channel_id=self.buffer.channel_id, due_at=due_at, video_url=video_url,
        )
        self.db.upsert_scheduled_post(
            video_id=video_id,
            buffer_post_id=post.post_id,
            state=post.status,
            scheduled_at=post.due_at,
            platform="tiktok",
        )
        if post.mock:
            next_status = "QUEUED"
            note = f"post agendado (mock): {post.post_id}"
        else:
            next_status = "QUEUED"
            note = f"post agendado no Buffer: {post.post_id}"

        self.sm.advance(video_id, next_status, actor=actor, note=note,
                        buffer_post_id=post.post_id,
                        buffer_post_state=post.status)
        return ExecutionResult(video_id, current, "publish", next_status, True, note)

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #
    def _previous_topics(self) -> list[str]:
        """Tópicos já usados, para evitar repetições."""
        topics: list[str] = []
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT topic FROM videos").fetchall()
        for r in rows:
            t = r["topic"]
            if t and t not in topics:
                topics.append(t)
        return topics

    def status(self, video_id: str) -> Optional[dict]:
        row = self.db.get_video(video_id)
        if not row:
            return None
        return dict(row)

    def list_by_status(self, status: str):
        return self.db.list_by_status(status)

    def close(self) -> None:
        self.db.close()


__all__ = ["Orchestrator", "ExecutionResult", "STAGE_ORDER"]
