"""Agente visual: monta o storyboard e gerencia assets visuais por cena (CP23).

Evolução do agente original (CP07):

- Consome ``script`` e ``idea`` do estado.
- Deriva prompts visuais do roteiro (SceneAssetManager.build_visual_prompts).
- Gera/reaproveita assets visuais por cena (SceneAssetManager.generate_assets)
  com cache por hash de conteúdo e provedor de imagem configurável.
- Registra proveniência, licença e custo de cada asset.
- Identidade visual consistente de personagens (CharacterIdentity).
- Fallback seguro quando o provedor falha.
- Integra à renderização atual (ArtCraft, FilmCraft, HyperFrames) sem
  substituí-los: devolve storyboard + scene_assets que o renderer consome.
- Avança current_stage para VISUAL_READY.
"""

from __future__ import annotations

from typing import Any

from src.workflow.agents.base import BaseAgent
from src.workflow.assets import MockImageProvider, SceneAssetManager
from src.workflow.state import Stage


class VisualAgent(BaseAgent):
    """Define os elementos visuais e os assets por cena do vídeo.

    Responsabilidades (CP23):
    - Prompts visuais derivados do roteiro.
    - Provedor configurável de imagem por IA (mock em modo offline).
    - Personagens e identidade visual consistentes.
    - Cache de imagens por hash de conteúdo.
    - Reaproveitamento de recursos existentes.
    - Associação entre cena, prompt, modelo e asset.
    - Registro de proveniência, licença e custo.
    - Fallback seguro quando o provedor falhar.
    - Integra à renderização atual sem substituir ArtCraft/FilmCraft/HyperFrames.
    """

    def __init__(self, provider=None) -> None:
        super().__init__("VisualAgent")
        self.provider = provider or MockImageProvider()
        self.manager = SceneAssetManager(self.provider)

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        state = self._ensure_state(state)
        script = state.get("script") or {}
        scenes = script.get("scenes", []) if isinstance(script, dict) else []
        topic = state.get("topic") or "a definir"

        # Personagem de marca consistente (se configurado no estado).
        brand = state.get("brand_character")
        if brand and isinstance(brand, dict):
            self.manager.attach_character(
                state,
                name=str(brand.get("name", "marca")),
                description=str(brand.get("description", "")),
                visual_traits=list(brand.get("visual_traits", [])),
                reference_asset_id=brand.get("reference_asset_id"),
            )

        # 1. Prompts visuais derivados do roteiro.
        prompts = self.manager.build_visual_prompts(state)

        # 2. Geração/reaproveitamento de assets por cena.
        assets = self.manager.generate_assets(state)

        # Índice rápido: scene_index -> asset_id (para o storyboard).
        asset_by_scene: dict[int, str] = {}
        for a in assets:
            asset_by_scene[a.scene_index] = a.asset_id

        # 3. Storyboard (compatível com o pipeline atual de renderização).
        # kind pode estar presente com valor None no estado; use "or" para o default.
        kind = state.get("kind") or "curiosidade"
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
                    "asset_id": asset_by_scene.get(idx, ""),
                }
                for idx, s in enumerate(scenes)
            ],
            "palette": {"bg": "#000000", "text": "#ffffff", "accent": "#ff0000"},
            "total_seconds": script.get("total_seconds", len(scenes) * 5.0),
            "visual_prompts": [p.model_dump(mode="json") for p in prompts],
            "scene_assets": [a.model_dump(mode="json") for a in assets],
        }

        state["storyboard"] = storyboard
        state["current_stage"] = "VISUAL_READY"
        state["status"] = "VISUAL_READY"
        state["_next"] = "supervisor"
        state["_step"] = "visual_agent"
        self._add_cost(state, self.name, "storyboard", tokens=20, cost_usd=0.0)
        return self._mark(
            state,
            self.name,
            "visual",
            f"Storyboard: template '{template_name}', "
            f"{len(storyboard['scenes'])} cenas, {len(assets)} assets",
            topic=topic,
            template=template_name,
            asset_count=len(assets),
        )


__all__ = ["VisualAgent"]
