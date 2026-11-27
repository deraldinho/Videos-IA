"""Testes de cobertura para src/workflow/agents/visual.py (VisualAgent).

Sem naveador real, sem API externa: o agente apenas monta o storyboard a partir
do roteiro e avança o estado para VISUAL_READY.
"""

from __future__ import annotations

from src.workflow.agents.visual import VisualAgent


def _state(scenes, topic="curiosidades", kind="curiosidade"):
    return {
        "execution_id": "exec-1",
        "video_id": "video-1",
        "topic": topic,
        "kind": kind,
        "script": {
            "scenes": scenes,
            "total_seconds": sum(s.get("seconds", 5) for s in scenes),
        },
        "current_stage": "SCRIPT_READY",
        "status": "SCRIPT_READY",
    }


def test_visual_agent_produz_storyboard_com_cenas():
    agent = VisualAgent()
    state = _state(
        [
            {"role": "gancho", "seconds": 3, "heading": "Gancho", "narration": ["Olá"]},
            {"role": "corpo", "seconds": 8, "heading": "Corpo", "narration": ["Texto"]},
            {"role": "cta", "seconds": 5, "heading": "CTA", "narration": ["Fim"]},
        ],
    )
    out = agent.run(state)

    assert out["current_stage"] == "VISUAL_READY"
    assert out["status"] == "VISUAL_READY"
    storyboard = out["storyboard"]
    assert storyboard["template"] == "curiosity"
    assert storyboard["topic"] == "curiosidades"
    assert len(storyboard["scenes"]) == 3
    assert storyboard["scenes"][0]["role"] == "gancho"
    assert storyboard["scenes"][0]["seconds"] == 3
    assert storyboard["scenes"][0]["heading"] == "Gancho"
    assert storyboard["palette"] == {"bg": "#000000", "text": "#ffffff", "accent": "#ff0000"}
    assert storyboard["total_seconds"] == 16


def test_visual_agent_escolhe_template_por_kind():
    agent = VisualAgent()
    cases = {
        "curiosidade": "curiosity",
        "lista": "lista",
        "historia": "historia",
        "explicacao": "explicacao",
        "qa": "qa",
        "desconhecido": "curiosidade",
    }
    for kind, expected in cases.items():
        state = _state([{"role": "corpo", "seconds": 5, "heading": "X", "narration": ["Y"]}], kind=kind)
        out = agent.run(state)
        assert out["storyboard"]["template"] == expected, f"kind={kind}"


def test_visual_agent_cenas_sem_heading_usam_vazio():
    agent = VisualAgent()
    state = _state([{"role": "corpo", "seconds": 5}])  # sem heading
    out = agent.run(state)
    assert out["storyboard"]["scenes"][0]["heading"] == ""


def test_visual_agent_cenas_sem_seconds_usam_padrao_5():
    agent = VisualAgent()
    state = _state([{"role": "corpo", "heading": "X", "narration": ["Y"]}])  # sem seconds
    out = agent.run(state)
    assert out["storyboard"]["scenes"][0]["seconds"] == 5.0


def test_visual_agent_role_padrao_corpo():
    agent = VisualAgent()
    state = _state([{"seconds": 5, "heading": "X", "narration": ["Y"]}])  # sem role
    out = agent.run(state)
    assert out["storyboard"]["scenes"][0]["role"] == "corpo"


def test_visual_agent_layout_vertical_1080x1920():
    agent = VisualAgent()
    state = _state([{"role": "gancho", "seconds": 3, "heading": "X", "narration": ["Y"]}])
    out = agent.run(state)
    assert out["storyboard"]["scenes"][0]["layout"] == "vertical_1080x1920"


__all__ = []
