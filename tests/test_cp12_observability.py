"""Testes CP12: observabilidade, dashboard e auditoria final (revisão dos CPs)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from langgraph.types import Command
from src.workflow.graph import build_graph, new_checkpointer
from src.workflow.state import ApprovalStatus, Stage, empty_state, state_to_dict, dict_to_state
from src.app_logging import setup_logging
from src.metrics import metrics


# --------------------------------------------------------------------------- #
# Observabilidade                                                             #
# --------------------------------------------------------------------------- #
def test_logging_setup_no_errors():
    setup_logging(level="INFO")


def test_workflow_state_round_trip_with_new_fields():
    state = empty_state("exec-1", "v1", topic="t")
    state.mode = "AUTO"
    state.live_enabled = True
    state.auto_enabled = True
    state.kind = "factual"
    state.compliance = {"mode": "AUTO", "blocked": False, "checks": []}
    state.monitoring = {"status": "PUBLISHED", "total_tokens": 50}

    data = state_to_dict(state)
    assert data["mode"] == "AUTO"
    assert data["live_enabled"] is True
    assert data["auto_enabled"] is True
    assert data["kind"] == "factual"
    assert data["compliance"]["blocked"] is False
    assert data["monitoring"]["total_tokens"] == 50

    restored = dict_to_state(data)
    assert restored.mode == "AUTO"
    assert restored.live_enabled is True
    assert restored.auto_enabled is True
    assert restored.kind == "factual"
    assert restored.compliance["blocked"] is False
    assert restored.monitoring["total_tokens"] == 50


# --------------------------------------------------------------------------- #
# Grafo completo com todos os 12 agentes (revisão final CP05-CP10)            #
# --------------------------------------------------------------------------- #
def test_graph_full_pipeline_reaches_monitored_and_audit(tmp_path, monkeypatch):
    """Executa o grafo completo com MP4 real e verifica que chega ao fim."""
    import subprocess

    def _make_mp4(path: Path) -> None:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "mandelbrot=size=1080x1920:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=440:d=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
                "-c:a", "aac", "-shortest",
                str(path),
            ],
            capture_output=True,
            check=True,
        )

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
        config = {"configurable": {"thread_id": "thread-full"}}

        state = state_to_dict(empty_state("exec-full", "vid-full", topic="tema de teste"))
        state["mode"] = "REVIEW"
        state["live_enabled"] = False
        state["auto_enabled"] = False
        state["video_assets"] = [{"mp4_path": str(mp4)}]

        result = graph.invoke(state, config=config)
        assert "__interrupt__" in result

        final = graph.invoke(Command(resume="approved"), config=config)
        assert "__interrupt__" not in final
        assert final["approval_status"] == ApprovalStatus.APPROVED.value
        # Fluxo completo: aprovação → publicação → monitoramento → auditoria.
        assert final["status"] in {Stage.MONITORED.value if hasattr(Stage, "MONITORED") else "MONITORED"}
        assert any(h.get("action") == "audit_final" for h in final["audit_history"]), final.get("audit_history")


# --------------------------------------------------------------------------- #
# Segurança: sem credenciais no estado/código                                 #
# --------------------------------------------------------------------------- #
def test_state_contains_no_credentials():
    state = empty_state("exec-sec", "v-sec")
    data = state_to_dict(state)
    text = json.dumps(data, ensure_ascii=False).lower()
    for token in ("api_key", "api_secret", "password", "secret", "nvidia_api_key"):
        assert token not in text, f"credencial vazada no estado: {token}"


__all__ = []
