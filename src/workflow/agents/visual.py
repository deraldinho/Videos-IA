"""Agente visual: monta o storyboard/layout/elementos visuais do vídeo."""

from __future__ import annotations

from typing import Any

from src.workflow.agents.base import BaseAgent
from src.workflow.state import Stage


class VisualAgent(BaseAgent):
    """Define os elementos visuais e o layout do vídeo antes da renderização.

    Responsabilidades (CP07):
    - Consome ``script`` e ``idea`` do estado.
    - Produz ``storyboard`` com cenas, templates, cores, transições.
    - Escolhe o template HTML com base no tipo de conteúdo.
    - Avança current_stage para VISUAL_READY.
    """

    def __init__(self) -> None:
        super().__init__("VisualAgent")

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        state = self._ensure_state(state)
        script = state.get("script") or {}
        scenes = script.get("scenes", []) if isinstance(script, dict) else []
        topic = state.get("topic") or "a definir"

        # Escolhe o template com base no tópico/kind (heurística simples).
        kind = state.get("kind", "curiosidade")
        template_map = {
            "curiosidade": "curiosity",
            "lista": "lista",
            "historia": "historia",
            "explicacao": "explicacao",
            "qa": "qa",
        }
        template_name = template_map.get(kind, "curiosidade")

        storyboard = {
            "template": template_name,
            "topic": topic,
            "scenes": [
                {
                    "role": s.get("role", "corpo") if isinstance(s, dict) else "corpo",
                    "seconds": s.get("seconds", 5.0) if isinstance(s, dict) else 5.0,
                    "heading": s.get("heading", "") if isinstance(s, dict) else "",
                    "layout": "vertical_1080x1920",
                }
                for s in scenes
            ],
            "palette": {"bg": "#000000", "text": "#ffffff", "accent": "#ff0000"},
            "total_seconds": script.get("total_seconds", len(scenes) * 5.0),
        }

        state["storyboard"] = storyboard
        state["current_stage"] = "VISUAL_READY"
        state["status"] = "VISUAL_READY"
        state["_next"] = "supervisor"
        state["_step"] = "visual_agent"
        self._add_cost(state, self.name, "storyboard", tokens=20, cost_usd=0.0)
        return self._mark(state, self.name, "visual",
                          f"Storyboard: template '{template_name}', {len(storyboard['scenes'])} cenas",
                          topic=topic, template=template_name)
