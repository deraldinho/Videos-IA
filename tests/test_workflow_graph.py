"""Testes do grafo LangGraph com 12 agentes especializados (CP05–CP10).

Cobre:
- Compilação do grafo (supervisor + 14 nós de agentes).
- Roteamento determinístico do supervisor através de todos os estágios.
- Cada agente especializado (research, idea, factcheck, script, voice, visual,
  render, transcription, quality, compliance, publishing, monitoring, audit).
- Execução até a interrupção de aprovação humana (interrupt).
- Retomada após aprovação (Command(resume=...)) até publicação, monitoramento,
  auditoria e fim.
- Testes unitários de cada agente.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from langgraph.types import Command

from src.workflow.graph import (
    MAX_RETRIES,
    build_graph,
    human_approval,
    new_checkpointer,
    quality_agent,
    script_agent,
    supervisor,
    audit_agent,
)
from src.workflow.state import (
    ApprovalStatus,
    Stage,
    empty_state,
    state_to_dict,
)


def _make_mp4(path: Path) -> None:
    """Gera um MP4 válido (1080x1920, 30fps, com áudio audível e variação visual).

    Usa ``mandelbrot`` (fractal animado, variação perceptível entre frames)
    em vez de padrões estáticos (testsrc/smptebars) para não disparar o
    quality gate de cena estática (pHash) em strict=True. Inclui stream de áudio
    para atender require_audio.
    """
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "mandelbrot=size=1080x1920:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:d=2",
            "-t", "2", "-r", "30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


def _initial_state(video_id: str = "vid-graph", topic: str = "tema de teste") -> dict:
    return {
        "execution_id": "exec-graph",
        "video_id": video_id,
        "topic": topic,
        "status": "IDEA_CREATED",
        "current_stage": "IDEA_CREATED",
        "mode": "REVIEW",
        "live_enabled": False,
        "auto_enabled": False,
    }


# --------------------------------------------------------------------------- #
# Supervisor: roteamento por estágio                                          #
# --------------------------------------------------------------------------- #
def test_supervisor_routes_to_research_when_idea_created():
    state = _initial_state()
    out = supervisor(state)
    assert out["_next"] == "research_agent"
    assert out["status"] == "IDEA_RESEARCHED"


def test_supervisor_routes_to_idea_when_researched():
    state = _initial_state()
    state["status"] = "IDEA_RESEARCHED"
    out = supervisor(state)
    assert out["_next"] == "idea_agent"
    assert out["status"] == "IDEA_READY"


def test_supervisor_routes_to_factcheck_when_idea_ready():
    state = _initial_state()
    state["status"] = "IDEA_READY"
    out = supervisor(state)
    assert out["_next"] == "factcheck_agent"
    assert out["status"] == "FACTCHECKED"


def test_supervisor_routes_to_script_when_factchecked():
    state = _initial_state()
    state["status"] = "FACTCHECKED"
    out = supervisor(state)
    assert out["_next"] == "script_agent"
    assert out["status"] == "SCRIPT_READY"


def test_supervisor_routes_to_voice_when_script_ready():
    state = _initial_state()
    state["status"] = "SCRIPT_READY"
    state["script"] = {"title": "T", "scenes": []}
    out = supervisor(state)
    assert out["_next"] == "voice_agent"
    assert out["status"] == "VOICE_READY"


def test_supervisor_routes_to_visual_when_voice_ready():
    state = _initial_state()
    state["status"] = "VOICE_READY"
    out = supervisor(state)
    assert out["_next"] == "visual_agent"
    assert out["status"] == "VISUAL_READY"


def test_supervisor_routes_to_render_when_visual_ready():
    state = _initial_state()
    state["status"] = "VISUAL_READY"
    out = supervisor(state)
    assert out["_next"] == "render_agent"
    assert out["status"] == "RENDERING"


def test_supervisor_routes_to_transcription_when_rendered():
    state = _initial_state()
    state["status"] = "RENDERED"
    state["video_assets"] = [{"mp4_path": "x.mp4"}]
    out = supervisor(state)
    assert out["_next"] == "transcription_agent"
    assert out["status"] == "TRANSCRIBED"


def test_supervisor_routes_to_quality_when_transcribed():
    state = _initial_state()
    state["status"] = "TRANSCRIBED"
    out = supervisor(state)
    assert out["_next"] == "quality_agent"
    assert out["status"] == "VALIDATED"


def test_supervisor_routes_to_compliance_when_validated():
    state = _initial_state()
    state["status"] = "VALIDATED"
    state["validation_results"] = {"valid": True}
    out = supervisor(state)
    assert out["_next"] == "compliance_agent"
    assert out["status"] == "COMPLIANCE_CHECKED"


def test_supervisor_routes_to_human_approval_when_compliance_checked():
    state = _initial_state()
    state["status"] = "COMPLIANCE_CHECKED"
    state["approval_status"] = "pending"
    out = supervisor(state)
    assert out["_next"] == "human_approval"
    assert out["status"] == "AWAITING_APPROVAL"


def test_supervisor_routes_to_publishing_when_approved():
    state = _initial_state()
    state["status"] = "APPROVED"
    state["approval_status"] = "approved"
    out = supervisor(state)
    assert out["_next"] == "publishing_agent"
    assert out["status"] == "UPLOADING"


def test_supervisor_routes_to_monitoring_when_published():
    state = _initial_state()
    state["status"] = "PUBLISHED"
    out = supervisor(state)
    assert out["_next"] == "monitoring_agent"
    assert out["status"] == "MONITORED"


def test_supervisor_routes_to_audit_when_monitored():
    state = _initial_state()
    state["status"] = "MONITORED"
    out = supervisor(state)
    assert out["_next"] == "audit_agent"


def test_supervisor_routes_to_audit_when_already_approved():
    state = _initial_state()
    state["status"] = "VALIDATED"
    state["validation_results"] = {"valid": True}
    state["approval_status"] = "approved"
    out = supervisor(state)
    assert out["_next"] == "audit_agent"


def test_supervisor_encerra_em_falho_quando_retries_esgotados():
    """Esgotadas as retentativas, o supervisor encerra em FAILED (não faz loop infinito).

    Antes da correção, um estágio em falha repetida (ex.: render sem o navegador
    headless) fazia o supervisor rotear para si mesmo indefinidamente no RENDERING.
    Agora MAX_RETRIES é aplicado e o gráfico termina em FAILED em vez de loopar.
    """
    state = _initial_state("vid-retries-exhaustos")
    state["status"] = Stage.RENDERING.value
    state["retries"] = MAX_RETRIES  # esgotado
    state["video_assets"] = []  # sem MP4 -> antes fazia loop infinito no RENDERING
    out = supervisor(state)
    assert out["status"] == Stage.FAILED.value
    assert out["_next"] == "__end__"


# --------------------------------------------------------------------------- #
# Agentes unitários                                                           #
# --------------------------------------------------------------------------- #
def test_research_agent_collects_sources():
    from src.workflow.agents.research import ResearchAgent
    state = _initial_state("vid-research", "curiosidade tech")
    out = ResearchAgent()(state)
    assert out["research_sources"]
    assert len(out["research_sources"]) == 2
    assert out["_next"] == "supervisor"
    assert out["status"] == "IDEA_RESEARCHED"


def test_idea_agent_generates_idea():
    from src.workflow.agents.idea import IdeaAgent
    state = _initial_state("vid-idea", "curiosidade tech")
    state["research_sources"] = [{"title": "Fonte A"}, {"title": "Fonte B"}]
    out = IdeaAgent()(state)
    assert out["idea"] is not None
    assert out["idea"]["topic"] == "curiosidade tech"
    assert out["status"] == "IDEA_READY"


def test_factcheck_agent_verdict_ok():
    from src.workflow.agents.factcheck import FactCheckAgent
    state = _initial_state("vid-fc", "tema")
    state["script"] = {"title": "T", "scenes": [{"role": "gancho", "narration": ["oi"]}]}
    out = FactCheckAgent()(state)
    assert out["factcheck"]["verdict"] == "ok"
    assert out["status"] == "FACTCHECKED"


def test_script_agent_produces_script():
    state = _initial_state()
    out = script_agent(state)
    assert out["script"] is not None
    assert out["script"]["title"] == "Roteiro automático"
    assert out["status"] == "SCRIPT_READY"


def test_voice_agent_creates_audio_assets(tmp_path):
    from src.workflow.agents.voice import VoiceAgent
    state = _initial_state("vid-voice", "tema")
    state["script"] = {
        "title": "T",
        "scenes": [{"role": "gancho", "seconds": 5.0, "heading": "H",
                    "narration": ["Olá, bem-vindo."], "source_ids": []}],
        "total_seconds": 10.0,
    }
    out = VoiceAgent()(state)
    assert out["audio_assets"]
    assert out["status"] == "VOICE_READY"


def test_visual_agent_creates_storyboard():
    from src.workflow.agents.visual import VisualAgent
    state = _initial_state("vid-visual", "tema")
    state["script"] = {
        "title": "T",
        "scenes": [{"role": "gancho", "seconds": 5.0, "heading": "H",
                    "narration": ["oi"], "source_ids": []}],
        "total_seconds": 10.0,
    }
    out = VisualAgent()(state)
    assert out["storyboard"] is not None
    assert out["storyboard"]["template"] == "curiosity"
    assert out["status"] == "VISUAL_READY"


def test_render_agent_produces_mp4(tmp_path):
    from src.workflow.agents.render import RenderAgent
    state = _initial_state("vid-render", "tema")
    state["script"] = {
        "title": "T",
        "scenes": [{"role": "gancho", "seconds": 5.0, "heading": "H",
                    "narration": ["Olá"], "source_ids": []}],
        "total_seconds": 10.0,
        "hashtags": ["#ia"],
        "description": "desc",
    }
    state["storyboard"] = {"template": "curiosity", "total_seconds": 10.0}
    state["audio_assets"] = []
    out = RenderAgent()(state)
    assert out["video_assets"]
    assert out["video_assets"][0]["mp4_path"].endswith(".mp4")
    assert out["status"] == "RENDERED"


def test_transcription_agent_transcribes(tmp_path):
    from src.workflow.agents.transcription import TranscriptionAgent
    state = _initial_state("vid-transcribe", "tema")
    state["script"] = {
        "scenes": [{"role": "gancho", "narration": ["Olá, bem-vindo."]}],
    }
    # Cria um WAV real para transcrever.
    import wave
    wav = tmp_path / "narracao.wav"
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(b"\x01\x02" * 22050)  # áudio não-silencioso
    state["audio_assets"] = [
        {"scene_role": "gancho", "index": 0, "text": "Olá, bem-vindo.",
         "duration_seconds": 3.0, "wav_path": str(wav)}
    ]
    out = TranscriptionAgent()(state)
    assert out["transcription"] is not None
    assert out["transcription"]["engine"] != "mock-asr"
    assert out["status"] == "TRANSCRIBED"


def test_quality_agent_validates_real_mp4(tmp_path):
    mp4 = tmp_path / "valido.mp4"
    _make_mp4(mp4)
    state = _initial_state()
    state["video_assets"] = [{"mp4_path": str(mp4)}]
    out = quality_agent(state)
    assert out["validation_results"]["valid"] is True
    assert out["validation_results"]["width"] == 1080
    assert out["validation_results"]["height"] == 1920
    assert out["validation_results"]["has_audio"] is True
    assert out["status"] == "VALIDATED"


def test_quality_agent_no_mp4_is_invalid():
    state = _initial_state()
    out = quality_agent(state)
    assert out["validation_results"]["valid"] is False
    assert out["validation_results"]["errors"]


def test_compliance_agent_review_not_blocked():
    from src.workflow.agents.compliance import ComplianceAgent
    state = _initial_state("vid-compliance", "tema")
    state["mode"] = "REVIEW"
    state["live_enabled"] = False
    state["auto_enabled"] = False
    out = ComplianceAgent()(state)
    assert out["compliance"]["blocked"] is False
    assert out["status"] == "COMPLIANCE_CHECKED"


def test_compliance_agent_auto_without_live_blocked():
    from src.workflow.agents.compliance import ComplianceAgent
    state = _initial_state("vid-compliance2", "tema")
    state["mode"] = "AUTO"
    state["live_enabled"] = False
    state["auto_enabled"] = True
    out = ComplianceAgent()(state)
    assert out["compliance"]["blocked"] is True


def test_publishing_agent_needs_mp4():
    from src.workflow.agents.publishing import PublishingAgent
    state = _initial_state("vid-publish", "tema")
    out = PublishingAgent()(state)
    assert out["errors"]
    assert "MP4 não encontrado" in out["errors"][-1]["message"]


def test_monitoring_agent_records_metrics():
    from src.workflow.agents.monitoring import MonitoringAgent
    state = _initial_state("vid-monitor", "tema")
    state["status"] = "PUBLISHED"
    state["costs"] = [{"agent": "A", "operation": "op", "tokens": 10, "cost_usd": 0.001}]
    state["errors"] = []
    state["audit_history"] = []
    state["publication_status"] = "published"
    out = MonitoringAgent()(state)
    assert out["monitoring"] is not None
    assert out["monitoring"]["total_tokens"] == 10
    assert out["status"] == "MONITORED"


def test_audit_agent_records_final_entry():
    state = _initial_state("vid-audit", "tema")
    state["status"] = "MONITORED"
    state["approval_status"] = "approved"
    out = audit_agent(state)
    assert any(h.get("action") == "audit_final" for h in out["audit_history"])
    assert out["_next"] == "__end__"


# --------------------------------------------------------------------------- #
# Execução do grafo                                                           #
# --------------------------------------------------------------------------- #
def test_graph_compiles_and_nodes_present():
    graph = build_graph()
    expected = {
        "supervisor", "research_agent", "idea_agent", "factcheck_agent",
        "script_agent", "voice_agent", "visual_agent", "render_agent",
        "transcription_agent", "quality_agent", "compliance_agent",
        "human_approval", "publishing_agent", "monitoring_agent", "audit_agent",
    }
    assert expected.issubset(set(graph.nodes))


def test_graph_pauses_for_human_approval(tmp_path, monkeypatch):
    mp4 = tmp_path / "valido.mp4"
    _make_mp4(mp4)

    # Mock do HTMLRenderer para não depender de Chromium/Puppeteer (não instalado
    # neste ambiente). O teste valida a orquestração do grafo, não a render externa.
    import src.video.renderer as renderer_mod

    class _FakeRenderer:
        def render(self, *args, **kwargs):
            out = Path(kwargs.get("output_name", "fake.mp4"))
            out.parent.mkdir(parents=True, exist_ok=True)
            import shutil

            shutil.copy(str(mp4), str(out))
            return out

    monkeypatch.setattr(renderer_mod, "HTMLRenderer", lambda *a, **k: _FakeRenderer())

    db_path = tmp_path / "checkpoints.sqlite3"
    with new_checkpointer(str(db_path)) as checkpointer:
        graph = build_graph(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "thread-graph-1"}}

        state = _initial_state("vid-graph-1")
        state["video_assets"] = [{"mp4_path": str(mp4)}]

        result = graph.invoke(state, config=config)

        # O grafo deve parar na interrupção de aprovação humana.
        assert "__interrupt__" in result, f"esperava interrupção, obteve: {result}"
        interrupts = result["__interrupt__"]
        assert len(interrupts) == 1
        assert interrupts[0].value["video_id"] == "vid-graph-1"


def test_graph_resume_after_approval_reaches_audit_and_end(tmp_path, monkeypatch):
    mp4 = tmp_path / "valido.mp4"
    _make_mp4(mp4)

    # Mock do HTMLRenderer para não depender de Chromium/Puppeteer (não instalado
    # neste ambiente). O teste valida a orquestração do grafo, não a render externa.
    import src.video.renderer as renderer_mod

    class _FakeRenderer:
        def render(self, *args, **kwargs):
            out = Path(kwargs.get("output_name", "fake.mp4"))
            out.parent.mkdir(parents=True, exist_ok=True)
            import shutil

            shutil.copy(str(mp4), str(out))
            return out

    monkeypatch.setattr(renderer_mod, "HTMLRenderer", lambda *a, **k: _FakeRenderer())

    db_path = tmp_path / "checkpoints.sqlite3"
    with new_checkpointer(str(db_path)) as checkpointer:
        graph = build_graph(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "thread-graph-2"}}

        state = _initial_state("vid-graph-2")
        state["video_assets"] = [{"mp4_path": str(mp4)}]

        # Primeira execução: para na aprovação humana.
        result = graph.invoke(state, config=config)
        assert "__interrupt__" in result

        # Retomada com aprovação humana.
        final = graph.invoke(Command(resume="approved"), config=config)
        assert "__interrupt__" not in final
        assert final["approval_status"] == "approved"
        # O fluxo avança da aprovação → publicação → monitoramento → auditoria.
        assert final["status"] in {"APPROVED", "MONITORED"}
        # Auditoria executada após a aprovação (via publicação → monitoramento → auditoria).
        assert any(
            h.get("action") == "audit_final" for h in final["audit_history"]
        ), final.get("audit_history")


def test_graph_full_run_reaches_end_without_interrupt_when_rejected(tmp_path, monkeypatch):
    """Quando rejeitado na aprovação humana, o grafo vai para ACTION_REQUIRED e não publica."""
    mp4 = tmp_path / "valido.mp4"
    _make_mp4(mp4)

    # Mock do HTMLRenderer para não depender de Chromium/Puppeteer (não instalado
    # neste ambiente). O teste valida a orquestração do grafo, não a render externa.
    import src.video.renderer as renderer_mod

    class _FakeRenderer:
        def render(self, *args, **kwargs):
            out = Path(kwargs.get("output_name", "fake.mp4"))
            out.parent.mkdir(parents=True, exist_ok=True)
            import shutil

            shutil.copy(str(mp4), str(out))
            return out

    monkeypatch.setattr(renderer_mod, "HTMLRenderer", lambda *a, **k: _FakeRenderer())

    db_path = tmp_path / "checkpoints.sqlite3"
    with new_checkpointer(str(db_path)) as checkpointer:
        graph = build_graph(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "thread-graph-3"}}

        state = _initial_state("vid-graph-3")
        state["video_assets"] = [{"mp4_path": str(mp4)}]

        result = graph.invoke(state, config=config)
        assert "__interrupt__" in result

        # Rejeita a aprovação.
        final = graph.invoke(Command(resume="rejected"), config=config)
        assert "__interrupt__" not in final
        assert final["approval_status"] == "rejected"
        assert final["status"] == "ACTION_REQUIRED"
        # Não deve ter publicado.
        assert final.get("publication_status") != "published"


def test_max_retries_constant_is_positive():
    assert MAX_RETRIES >= 1


def test_empty_state_round_trip():
    state = empty_state("exec-1", "v1", topic="t")
    data = state_to_dict(state)
    assert isinstance(data, dict)
    assert data["execution_id"] == "exec-1"
    assert data["mode"] == "REVIEW"
    assert data["live_enabled"] is False
    assert data["auto_enabled"] is False


__all__ = []
