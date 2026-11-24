"""Grafo LangGraph com 12 agentes especializados + supervisor (CP05–CP10).

Orquestra o pipeline completo de produção de vídeo:

  supervisor → research → idea → factcheck → script → voice → visual →
  render → transcription → quality → compliance → human_approval →
  publishing → monitoring → audit → END

Cada nó recebe e devolve o estado completo (dict compatível com os contratos
Pydantic de :mod:`src.workflow.state`). A transição entre nós é determinística:
o Supervisor roteia com base no estágio atual e cada agente reporta de volta ao
Supervisor, que decide o próximo passo. Limites de retentativa impedem ciclos
infinitos. Checkpoints persistentes via SqliteSaver e interrupção persistente
para aprovação humana (human-in-the-loop).
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph, START
from langgraph.types import Command, interrupt

from src.workflow.events import emit, ACTION_STARTED, ACTION_COMPLETED, ACTION_ERROR
from src.workflow.state import (
    ApprovalStatus,
    Stage,
    WorkflowState,
    _utc_now_iso,
    dict_to_state,
    empty_state,
    state_to_dict,
)

# Limite de retentativas por estágio antes de encerrar com erro.
MAX_RETRIES = 3


def _instrumented(agent_name: str):
    """Decora um nó de agente para emitir eventos de início/término/erro no barramento."""

    def decorator(fn):
        def wrapper(state, *args, **kwargs):
            state = _ensure_state(state)
            emit(agent_name, ACTION_STARTED, state,
                 stage=state.get("current_stage"), status=state.get("status"))
            try:
                out = fn(state, *args, **kwargs)
                out = out if isinstance(out, dict) else state
                emit(
                    agent_name,
                    ACTION_COMPLETED,
                    out,
                    stage=out.get("current_stage"),
                    status=out.get("status"),
                    note=out.get("note"),
                )
                return out
            except Exception as exc:  # noqa: BLE001 - propaga o erro após emitir
                emit(agent_name, ACTION_ERROR, state,
                     stage=state.get("current_stage"), status=state.get("status"),
                     note=str(exc))
                raise

        return wrapper

    return decorator

# Nomes dos nós.
NODE_SUPERVISOR = "supervisor"
NODE_RESEARCH = "research_agent"
NODE_IDEA = "idea_agent"
NODE_FACTCHECK = "factcheck_agent"
NODE_SCRIPT = "script_agent"
NODE_VOICE = "voice_agent"
NODE_VISUAL = "visual_agent"
NODE_RENDER = "render_agent"
NODE_TRANSCRIPTION = "transcription_agent"
NODE_QUALITY = "quality_agent"
NODE_COMPLIANCE = "compliance_agent"
NODE_HUMAN_APPROVAL = "human_approval"
NODE_PUBLISHING = "publishing_agent"
NODE_MONITORING = "monitoring_agent"
NODE_AUDIT = "audit_agent"
NODE_PUBLICATION_TARGETS = "publication_targets"
NODE_ANALYTICS = "analytics"

# Ordem determinística de estágios (supervisor roteia nesta sequência).
STAGE_ORDER = [
    Stage.IDEA_CREATED.value,
    "IDEA_RESEARCHED",
    "IDEA_READY",
    "FACTCHECKED",
    Stage.SCRIPT_READY.value,
    "VOICE_READY",
    "VISUAL_READY",
    Stage.RENDERED.value,
    "TRANSCRIBED",
    Stage.VALIDATED.value,
    "COMPLIANCE_CHECKED",
    Stage.AWAITING_APPROVAL.value,
    Stage.APPROVED.value,
    Stage.PUBLISHED.value,
    Stage.QUEUED.value,
    "MONITORED",
    "TARGETS_PREPARADO",
    "ANALYTICAS",
]


# --------------------------------------------------------------------------- #
# Helpers de estado                                                           #
# --------------------------------------------------------------------------- #
def _ensure_state(state: dict[str, Any]) -> dict[str, Any]:
    """Garante que o estado dicionário tenha os campos básicos preenchidos."""
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
    if state.get("started_at") is None:
        state["started_at"] = _utc_now_iso()
    if state.get("updated_at") is None:
        state["updated_at"] = _utc_now_iso()
    return state


def _retries_left(state: dict[str, Any]) -> bool:
    return int(state.get("retries", 0)) < MAX_RETRIES


def _status_value(state: dict[str, Any], key: str, default: str) -> str:
    """Normaliza o valor de estágio/status, aceitando Enum ou string."""
    raw = state.get(key, default)
    if isinstance(raw, Enum):
        return raw.value
    return str(raw) if raw is not None else default


# --------------------------------------------------------------------------- #
# Supervisor                                                                  #
# --------------------------------------------------------------------------- #
def supervisor(state: dict[str, Any]) -> dict[str, Any]:
    """Roteia para o próximo agente com base no estágio atual."""
    state = _ensure_state(state)
    status = _status_value(state, "status", Stage.IDEA_CREATED.value)
    approval = _status_value(state, "approval_status", ApprovalStatus.PENDING.value)
    compliance = state.get("compliance") or {}
    blocked = bool(compliance.get("blocked"))

    # Limite de retentativas (MAX_RETRIES). Quando as tentativas se esgotam
    # e o vídeo ainda não atingiu um estado terminal nem está à espera de
    # decisão humana, encerra em FAILED. Sem isto, um estágio que falha
    # repetidamente (ex.: render sem navegador headless) faz o supervisor
    # rotear para si mesmo indefinidamente (loop infinito). MAX_RETRIES e
    # _retries_left() estavam definidos mas nunca eram aplicados.
    if (
        not _retries_left(state)
        and status
        not in {
            Stage.FAILED.value,
            Stage.PUBLISHED.value,
            Stage.AWAITING_APPROVAL.value,
            Stage.ACTION_REQUIRED.value,
        }
    ):
        state["_next"] = "__end__"
        state["status"] = Stage.FAILED.value
        return state

    # CP27 — Recuperação de falha: se houve erro recente (errors não vazio),
    # há retries restantes e um agente está em execução (_step), roteia de
    # volta para esse agente para nova tentativa, evitando loop infinito.
    # Só se aplica quando o status ainda está em estágio de processamento
    # (não chegou a um estado de conclusão como VALIDATED/APPROVED/PUBLISHED).
    step = state.get("_step")
    errors = state.get("errors") or []
    retries = int(state.get("retries", 0))
    status_conclusao = {
        Stage.VALIDATED.value, Stage.APPROVED.value, Stage.UPLOADED.value,
        Stage.QUEUED.value, Stage.PUBLISHED.value, Stage.RENDERED.value,
        Stage.FAILED.value, Stage.ACTION_REQUIRED.value,
    }
    if (
        step
        and errors
        and retries < MAX_RETRIES
        and status not in status_conclusao
    ):
        state["retries"] = retries + 1
        state["_next"] = step
        return state

    # Publicacao concluida (real PUBLISHED ou agendado/mock QUEUED):
    # monitoramento -> analitica -> auditoria -> fim.
    # Preserva QUEUED (post agendado) nao sobrescrevendo com MONITORED:
    # MONITORED e um status de controle do supervisor, nao um estado de video.
    if status in {Stage.PUBLISHED.value, Stage.QUEUED.value}:
        estado_publicacao = status
        state["_next"] = NODE_MONITORING
        if status == Stage.PUBLISHED.value:
            # Ao entrar no monitoramento a partir de PUBLISHED, o status de
            # controle do supervisor e MONITORED (nao deixa PUBLISHED, que e
            # um estado de vídeo; MONITORED indica que o monitoramento roda).
            estado_publicacao = "MONITORED"
        state["status"] = estado_publicacao
        state["current_stage"] = estado_publicacao
        return state
    if status == "MONITORED":
        # CP25: apos o monitoramento, coletar metricas antes da auditoria.
        # So vai para analytics quando o monitoramento acabou de rodar
        # (_step == monitoring_agent). analytics ja rodou (_step == analytics_agent)
        # ou um MONITORED simples (sem origem conhecida) vai direto à auditoria,
        # preservando o roteamento anterior (MONITORED -> audit).
        if state.get("_step") == "monitoring_agent":
            state["_next"] = NODE_ANALYTICS
            return state
        if state.get("_step") == "analytics_agent":
            state["_next"] = NODE_AUDIT
            return state
        state["_next"] = NODE_AUDIT
        return state

    # Ja aprovado (mas ainda nao publicou): preparar targets multicanal, depois publicar.
    if approval == ApprovalStatus.APPROVED.value and status == Stage.APPROVED.value:
        if state.get("targets_pending"):
            # CP24: preparar targets multicanal antes de publicar em cada um.
            state["targets_pending"] = False
            state["_next"] = NODE_PUBLICATION_TARGETS
            return state
        state["_next"] = NODE_PUBLISHING
        state["status"] = Stage.UPLOADING.value
        return state

    # Já aprovado via fluxo anterior (ex.: validation + approved): ir direto à auditoria.
    if approval == ApprovalStatus.APPROVED.value:
        state["_next"] = NODE_AUDIT
        return state

    # Conformidade verificada e não bloqueada: aguardar aprovação humana.
    if (
        status == "COMPLIANCE_CHECKED"
        and approval != ApprovalStatus.APPROVED.value
        and not blocked
    ):
        state["_next"] = NODE_HUMAN_APPROVAL
        state["status"] = Stage.AWAITING_APPROVAL.value
        return state

    # Qualidade validada: conformidade (somente se a validação passou de fato).
    if status == Stage.VALIDATED.value:
        vr = state.get("validation_results") or {}
        if not vr.get("valid"):
            # Qualidade inválida: não avança para conformidade. Sem transição
            # aplicável -> encerra (falha não é sucesso, evita PUBLISHED sem
            # qualidade). O supervisor não encontra transição e termina.
            state["_next"] = "__end__"
            return state
        state["_next"] = NODE_COMPLIANCE
        state["status"] = "COMPLIANCE_CHECKED"
        return state

    # Transcrito: qualidade (não reexecutar se a validação já falhou — evita loop).
    if status == "TRANSCRIBED":
        vr = state.get("validation_results") or {}
        if vr and not vr.get("valid"):
            # Qualidade já falhou anteriormente: não ciclar de volta para
            # quality_agent. Sem transição aplicável -> encerra.
            state["_next"] = "__end__"
            return state
        state["_next"] = NODE_QUALITY
        state["status"] = Stage.VALIDATED.value
        return state

    # Renderizado: transcrição.
    if status == Stage.RENDERED.value:
        state["_next"] = NODE_TRANSCRIPTION
        state["status"] = "TRANSCRIBED"
        return state

    # Visual pronto: renderização.
    if status == "VISUAL_READY":
        state["_next"] = NODE_RENDER
        state["status"] = Stage.RENDERING.value
        return state

    # Renderização em andamento: continuar renderizar ou ir para transcrição.
    if status == Stage.RENDERING.value:
        video_assets = state.get("video_assets") or []
        if video_assets and video_assets[-1].get("mp4_path"):
            state["_next"] = NODE_TRANSCRIPTION
            state["status"] = "TRANSCRIBED"
        else:
            state["_next"] = NODE_RENDER
            state["status"] = Stage.RENDERING.value
        return state

    # Voz pronta: visual.
    if status == "VOICE_READY":
        state["_next"] = NODE_VISUAL
        state["status"] = "VISUAL_READY"
        return state

    # Roteiro pronto: voz.
    if status == Stage.SCRIPT_READY.value:
        state["_next"] = NODE_VOICE
        state["status"] = "VOICE_READY"
        return state

    # Fact-check concluído: roteiro.
    if status == "FACTCHECKED":
        state["_next"] = NODE_SCRIPT
        state["status"] = Stage.SCRIPT_READY.value
        return state

    # Ideia pronta: fact-check.
    if status == "IDEA_READY":
        state["_next"] = NODE_FACTCHECK
        state["status"] = "FACTCHECKED"
        return state

    # Pesquisado: ideia.
    if status == "IDEA_RESEARCHED":
        state["_next"] = NODE_IDEA
        state["status"] = "IDEA_READY"
        return state

    # Inicial / sem roteiro: pesquisa.
    if status == Stage.IDEA_CREATED.value:
        state["_next"] = NODE_RESEARCH
        state["status"] = "IDEA_RESEARCHED"
        return state

    # Nenhuma transição aplicável: encerrar.
    state["_next"] = "__end__"
    return state


# --------------------------------------------------------------------------- #
# Agentes especializados                                                      #
# --------------------------------------------------------------------------- #
def research_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Pesquisa fontes e dados sobre o tópico."""
    from src.workflow.agents.research import ResearchAgent

    state = _ensure_state(state)
    out = ResearchAgent()(state)
    out["_next"] = NODE_SUPERVISOR
    return out


def idea_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Gera a ideia original de roteiro a partir da pesquisa."""
    from src.workflow.agents.idea import IdeaAgent

    state = _ensure_state(state)
    out = IdeaAgent()(state)
    out["_next"] = NODE_SUPERVISOR
    return out


def factcheck_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Verifica fatos e fontes do roteiro."""
    from src.workflow.agents.factcheck import FactCheckAgent

    state = _ensure_state(state)
    out = FactCheckAgent()(state)
    out["_next"] = NODE_SUPERVISOR
    return out


def script_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Confirma/gera o roteiro do vídeo (caminho assisted: sem chamada LLM real)."""
    state = _ensure_state(state)
    script = state.get("script")
    if not script:
        script = {
            "topic": state.get("topic") or "a definir",
            "title": "Roteiro automático",
            "description": "Roteiro gerado pelo ScriptAgent (modo assisted).",
            "hashtags": ["#automacao", "#ia"],
            "scenes": [
                {"role": "gancho", "seconds": 5.0, "heading": "Abertura",
                 "narration": ["Olá, bem-vindo."], "source_ids": []},
                {"role": "cta", "seconds": 5.0, "heading": "Encerramento",
                 "narration": ["Até a próxima."], "source_ids": []},
            ],
            "total_seconds": 10.0,
        }
    state["script"] = script
    state["status"] = Stage.SCRIPT_READY.value
    state["current_stage"] = Stage.SCRIPT_READY.value
    state["_next"] = NODE_SUPERVISOR
    state["_step"] = "script_agent"
    return state


def voice_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Gera a narração TTS (Piper pt-BR) com fallback de silêncio."""
    from src.workflow.agents.voice import VoiceAgent

    state = _ensure_state(state)
    require_real = bool(state.get("tts_required", False))
    out = VoiceAgent(require_real_tts=require_real)(state)
    out["_next"] = NODE_SUPERVISOR
    return out


def visual_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Define elementos visuais e layout (storyboard)."""
    from src.workflow.agents.visual import VisualAgent

    state = _ensure_state(state)
    out = VisualAgent()(state)
    out["_next"] = NODE_SUPERVISOR
    return out


def render_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Renderiza o MP4 final (1080×1920, 30fps, H.264/AAC)."""
    from src.workflow.agents.render import RenderAgent

    state = _ensure_state(state)
    out = RenderAgent()(state)
    out["_next"] = NODE_SUPERVISOR
    return out


def transcription_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Transcreve o áudio gerado (ASR) para verificação de qualidade."""
    from src.workflow.agents.transcription import TranscriptionAgent

    state = _ensure_state(state)
    out = TranscriptionAgent()(state)
    out["_next"] = NODE_SUPERVISOR
    return out


def quality_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Valida qualidade do MP4 (reuso do FFprobeValidator) quando houver vídeo."""
    from src.video.validator import FFprobeValidator
    from src.settings import load_settings

    state = _ensure_state(state)
    mp4_path = state.get("video_assets", [{}])[-1].get("mp4_path") if state.get("video_assets") else None
    result: dict[str, Any] = {"valid": False, "errors": []}
    if mp4_path:
        from pathlib import Path

        validator = FFprobeValidator()
        try:
            settings = load_settings()
            width = settings.video.width
            height = settings.video.height
            fps = float(settings.video.fps)
        except Exception:  # noqa: BLE001 - settings indisponível, defaults seguros
            width, height, fps = 1080, 1920, 30.0
        try:
            info = validator.validate_tiktok(
                Path(mp4_path),
                width=width,
                height=height,
                fps=fps,
                require_audio=True,
                strict=True,
            )
            result = {
                "valid": True,
                "width": info.width,
                "height": info.height,
                "fps": info.fps,
                "duration_seconds": info.duration_seconds,
                "has_audio": info.has_audio,
                "errors": [],
            }
        except Exception as exc:  # noqa: BLE001 - validação falhou, registra e segue
            result = {"valid": False, "errors": [str(exc)]}
    else:
        result = {"valid": False, "errors": ["Nenhum MP4 para validar"]}
    state["validation_results"] = result
    if result["valid"]:
        state["status"] = Stage.VALIDATED.value
        state["current_stage"] = Stage.VALIDATED.value
    else:
        # Qualidade falhou: NÃO deixa status como VALIDATED (mesmo que tenha
        # sido VALIDADO em execução anterior). Uma falha de qualidade não é
        # sucesso. Mantém o estágio atual (ex.: TRANSCRIBED) para que o
        # supervisor não encontre transição aplicável e encerre em vez de
        # avançar prematuramente para compliance com qualidade inválida.
        estado_atual = state.get("current_stage", "TRANSCRIBED")
        state["status"] = estado_atual
        state["current_stage"] = estado_atual
    state["_next"] = NODE_SUPERVISOR
    state["_step"] = "quality_agent"
    return state


def compliance_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Aplica regras de conformidade (DRY_RUN/REVIEW/AUTO, aprovação)."""
    from src.workflow.agents.compliance import ComplianceAgent

    state = _ensure_state(state)
    out = ComplianceAgent()(state)
    out["_next"] = NODE_SUPERVISOR
    return out


def human_approval(state: dict[str, Any]) -> dict[str, Any]:
    """Interrompe persistente para aprovação humana (human-in-the-loop).

    O grafo para aqui e aguarda ``graph.invoke(Command(resume=<decisão>), ...)``.
    """
    state = _ensure_state(state)
    decision = interrupt(
        {
            "video_id": state.get("video_id"),
            "status": state.get("status"),
            "prompt": "Aprovar publicação deste vídeo? (approved/rejected)",
        }
    )
    state.pop("__interrupt__", None)
    state["approval_status"] = str(decision or "rejected")
    if state["approval_status"] == "approved":
        state["status"] = Stage.APPROVED.value
        state["current_stage"] = Stage.APPROVED.value
        # CP24: apos a aprovacao, preparar os targets multicanal antes de publicar.
        state["targets_pending"] = True
    else:
        state["status"] = Stage.ACTION_REQUIRED.value
        state["current_stage"] = Stage.ACTION_REQUIRED.value
    state["_next"] = NODE_SUPERVISOR
    state["updated_at"] = _utc_now_iso()
    return state


def publishing_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Upload Cloudinary + agendamento Buffer (mock sem live)."""
    from src.workflow.agents.publishing import PublishingAgent

    state = _ensure_state(state)
    out = PublishingAgent()(state)
    out["_next"] = NODE_SUPERVISOR
    return out


def monitoring_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Monitora execução, métricas e logs do pipeline."""
    from src.workflow.agents.monitoring import MonitoringAgent

    state = _ensure_state(state)
    out = MonitoringAgent()(state)
    out["_next"] = NODE_SUPERVISOR
    return out


def publication_targets_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Prepara PublicationTargets para todos os canais de publicacao (CP24).

    Usa PublicationManager para preparar targets por plataforma
    (tiktok, youtube_shorts, instagram_reels) com isolamento de falha
    e idempotencia. Apos preparar, volta ao supervisor, que roteia para
    o publishing_agent (publicar em cada target).
    """
    from src.workflow.publication import (
        InstagramReelsAdapter,
        PublicationManager,
        TikTokAdapter,
        YouTubeShortsAdapter,
    )

    state = _ensure_state(state)
    video_id = state["video_id"]
    platforms = ["tiktok", "youtube_shorts", "instagram_reels"]

    manager = PublicationManager(
        [
            TikTokAdapter(live=False),
            YouTubeShortsAdapter(live=False),
            InstagramReelsAdapter(live=False),
        ]
    )
    targets = manager.prepare_targets(
        state, video_id=video_id, platforms=platforms
    )

    # Garante que os targets estejam persistidos no estado (prepare_targets ja o faz)
    # e marca o fluxo como pronto para publicacao em cada target.
    state["publication_targets"] = [
        t.model_dump(mode="json") for t in targets
    ]
    state["status"] = Stage.APPROVED.value
    state["current_stage"] = Stage.APPROVED.value
    state["targets_pending"] = False
    state["_next"] = NODE_SUPERVISOR
    state["_step"] = "publication_targets_agent"
    return state


def analytics_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Coleta metricas de todos os targets publicados (CP25).

    Usa AnalyticsManager (MockAnalyticsProvider) para coletar metricas
    dos targets publicados/agendados e consolidar no estado
    (state['metric_snapshots']). Apos coletar, volta ao supervisor, que
    roteia para o audit_agent.
    """
    from src.workflow.analytics import AnalyticsManager, MockAnalyticsProvider

    state = _ensure_state(state)
    manager = AnalyticsManager([MockAnalyticsProvider()])
    state = manager.consolidate(state)

    state["status"] = "MONITORED"
    state["current_stage"] = "MONITORED"
    state["_next"] = NODE_SUPERVISOR
    state["_step"] = "analytics_agent"
    return state


def audit_agent(state: dict[str, Any]) -> dict[str, Any]:
    """Grava uma entrada de auditoria final no estado."""
    state = _ensure_state(state)
    history = list(state.get("audit_history", []) or [])
    history.append(
        {
            "actor": "system",
            "action": "audit_final",
            "note": "Workflow concluído",
            "timestamp": _utc_now_iso(),
            "fields": {
                "status": state.get("status"),
                "approval_status": state.get("approval_status"),
                "retries": state.get("retries"),
            },
        }
    )
    state["audit_history"] = history
    state["updated_at"] = _utc_now_iso()
    state["_next"] = "__end__"
    return state


# --------------------------------------------------------------------------- #
# Instrumentação de visibilidade em tempo real (CP29).
#
# Cada nó de agente é decorado para emitir eventos started/completed/error no
# barramento de eventos (src.workflow.events), consumidos pelo servidor web e
# pela UI /agents. O comportamento dos nós é preservado (o estado retornado é
# o mesmo); apenas são emitidos efeitos colaterais de observabilidade.
# --------------------------------------------------------------------------- #
research_agent = _instrumented(NODE_RESEARCH)(research_agent)
idea_agent = _instrumented(NODE_IDEA)(idea_agent)
factcheck_agent = _instrumented(NODE_FACTCHECK)(factcheck_agent)
script_agent = _instrumented(NODE_SCRIPT)(script_agent)
voice_agent = _instrumented(NODE_VOICE)(voice_agent)
visual_agent = _instrumented(NODE_VISUAL)(visual_agent)
render_agent = _instrumented(NODE_RENDER)(render_agent)
transcription_agent = _instrumented(NODE_TRANSCRIPTION)(transcription_agent)
quality_agent = _instrumented(NODE_QUALITY)(quality_agent)
compliance_agent = _instrumented(NODE_COMPLIANCE)(compliance_agent)
human_approval = _instrumented(NODE_HUMAN_APPROVAL)(human_approval)
publishing_agent = _instrumented(NODE_PUBLISHING)(publishing_agent)
monitoring_agent = _instrumented(NODE_MONITORING)(monitoring_agent)
publication_targets_agent = _instrumented(NODE_PUBLICATION_TARGETS)(publication_targets_agent)
analytics_agent = _instrumented(NODE_ANALYTICS)(analytics_agent)
audit_agent = _instrumented(NODE_AUDIT)(audit_agent)


# --------------------------------------------------------------------------- #
# Roteamento condicional                                                      #
# --------------------------------------------------------------------------- #
def _route(state: dict[str, Any]) -> str:
    nxt = state.get("_next", "__end__")
    valid_nodes = {
        NODE_RESEARCH, NODE_IDEA, NODE_FACTCHECK, NODE_SCRIPT, NODE_VOICE,
        NODE_VISUAL, NODE_RENDER, NODE_TRANSCRIPTION, NODE_QUALITY,
        NODE_COMPLIANCE, NODE_HUMAN_APPROVAL, NODE_PUBLICATION_TARGETS,
        NODE_PUBLISHING, NODE_MONITORING, NODE_ANALYTICS, NODE_AUDIT,
    }
    if nxt in valid_nodes:
        return nxt
    return "__end__"


# --------------------------------------------------------------------------- #
# Construção do grafo                                                         #
# --------------------------------------------------------------------------- #
def build_graph(checkpointer: SqliteSaver | None = None):
    """Compila o StateGraph com checkpointer opcional."""
    builder = StateGraph(dict)
    builder.add_node(NODE_SUPERVISOR, supervisor)
    builder.add_node(NODE_RESEARCH, research_agent)
    builder.add_node(NODE_IDEA, idea_agent)
    builder.add_node(NODE_FACTCHECK, factcheck_agent)
    builder.add_node(NODE_SCRIPT, script_agent)
    builder.add_node(NODE_VOICE, voice_agent)
    builder.add_node(NODE_VISUAL, visual_agent)
    builder.add_node(NODE_RENDER, render_agent)
    builder.add_node(NODE_TRANSCRIPTION, transcription_agent)
    builder.add_node(NODE_QUALITY, quality_agent)
    builder.add_node(NODE_COMPLIANCE, compliance_agent)
    builder.add_node(NODE_HUMAN_APPROVAL, human_approval)
    builder.add_node(NODE_PUBLISHING, publishing_agent)
    builder.add_node(NODE_MONITORING, monitoring_agent)
    builder.add_node(NODE_AUDIT, audit_agent)
    builder.add_node(NODE_PUBLICATION_TARGETS, publication_targets_agent)
    builder.add_node(NODE_ANALYTICS, analytics_agent)

    builder.add_edge(START, NODE_SUPERVISOR)
    builder.add_conditional_edges(
        NODE_SUPERVISOR,
        _route,
        {
            NODE_RESEARCH: NODE_RESEARCH,
            NODE_IDEA: NODE_IDEA,
            NODE_FACTCHECK: NODE_FACTCHECK,
            NODE_SCRIPT: NODE_SCRIPT,
            NODE_VOICE: NODE_VOICE,
            NODE_VISUAL: NODE_VISUAL,
            NODE_RENDER: NODE_RENDER,
            NODE_TRANSCRIPTION: NODE_TRANSCRIPTION,
            NODE_QUALITY: NODE_QUALITY,
            NODE_COMPLIANCE: NODE_COMPLIANCE,
            NODE_HUMAN_APPROVAL: NODE_HUMAN_APPROVAL,
            NODE_PUBLICATION_TARGETS: NODE_PUBLICATION_TARGETS,
            NODE_PUBLISHING: NODE_PUBLISHING,
            NODE_MONITORING: NODE_MONITORING,
            NODE_ANALYTICS: NODE_ANALYTICS,
            NODE_AUDIT: NODE_AUDIT,
            "__end__": END,
        },
    )
    builder.add_edge(NODE_RESEARCH, NODE_SUPERVISOR)
    builder.add_edge(NODE_IDEA, NODE_SUPERVISOR)
    builder.add_edge(NODE_FACTCHECK, NODE_SUPERVISOR)
    builder.add_edge(NODE_SCRIPT, NODE_SUPERVISOR)
    builder.add_edge(NODE_VOICE, NODE_SUPERVISOR)
    builder.add_edge(NODE_VISUAL, NODE_SUPERVISOR)
    builder.add_edge(NODE_RENDER, NODE_SUPERVISOR)
    builder.add_edge(NODE_TRANSCRIPTION, NODE_SUPERVISOR)
    builder.add_edge(NODE_QUALITY, NODE_SUPERVISOR)
    builder.add_edge(NODE_COMPLIANCE, NODE_SUPERVISOR)
    builder.add_edge(NODE_PUBLISHING, NODE_SUPERVISOR)
    builder.add_edge(NODE_MONITORING, NODE_SUPERVISOR)
    builder.add_edge(NODE_PUBLICATION_TARGETS, NODE_SUPERVISOR)
    builder.add_edge(NODE_ANALYTICS, NODE_SUPERVISOR)
    builder.add_edge(NODE_AUDIT, END)
    builder.add_edge(NODE_HUMAN_APPROVAL, NODE_SUPERVISOR)

    return builder.compile(checkpointer=checkpointer)


def new_checkpointer(db_path: str | None = None) -> SqliteSaver:
    """Cria um SqliteSaver persistente para checkpoints do grafo."""
    from pathlib import Path

    if db_path is None:
        db_path = str(Path("data") / "langgraph_checkpoints.sqlite3")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver.from_conn_string(db_path)


__all__ = [
    "build_graph",
    "new_checkpointer",
    "MAX_RETRIES",
    "NODE_SUPERVISOR",
    "NODE_RESEARCH",
    "NODE_IDEA",
    "NODE_FACTCHECK",
    "NODE_SCRIPT",
    "NODE_VOICE",
    "NODE_VISUAL",
    "NODE_RENDER",
    "NODE_TRANSCRIPTION",
    "NODE_QUALITY",
    "NODE_COMPLIANCE",
    "NODE_HUMAN_APPROVAL",
    "NODE_PUBLISHING",
    "NODE_MONITORING",
    "NODE_AUDIT",
    "NODE_PUBLICATION_TARGETS",
    "NODE_ANALYTICS",
    "STAGE_ORDER",
]
