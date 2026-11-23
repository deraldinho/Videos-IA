"""Gerenciamento de assets visuais por cena (CP23).

Fornece:

* ``ImageProvider`` — interface de provedor de imagem por IA (configurável).
* ``MockImageProvider`` — provedor determinístico offline, sem chamada de rede,
  usado apenas em testes e modo não-live. Nunca apresenta imagens simuladas
  como reais de um provedor autorizado.
* ``SceneAssetManager`` — cria prompts visuais derivados do roteiro, gerencia
  cache de imagem por hash de conteúdo, reaproveita recursos existentes,
  registra proveniência/licença/custo e aplica fallback seguro quando o
  provedor falha.

Integra-se à renderização atual (ArtCraft, FilmCraft, HyperFrames) sem
substituí-los: o manager devolve um ``SceneAsset`` com ``url``/caminho que o
pipeline de render já consome.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from src.workflow.state import (
    AssetCacheEntry,
    AssetProvenance,
    CharacterIdentity,
    SceneAsset,
    VisualPrompt,
    _utc_now_iso,
)


@runtime_checkable
class ImageProvider(Protocol):
    """Provedor de geração de imagem por IA.

    ``generate`` recebe um prompt visual e retorna um dict com:
    ``url`` (caminho/URL da imagem gerada), ``model``, ``size_bytes``,
    ``width``, ``height``, ``format`` e ``cost_usd``.
    Implementações reais chamam a API do provedor (ex.: DALL-E, Midjourney,
    Stable Diffusion) apenas com credenciais e autorização explícita.
    """

    def generate(self, prompt: str, model: str,
                 size: str = "1080x1920") -> dict[str, Any]:  # pragma: no cover - interface
        ...


@dataclass
class GeneratedImage:
    """Resultado bruto da geração de imagem por um provedor."""

    url: str
    model: str
    width: int
    height: int
    size_bytes: int
    format: str
    cost_usd: float = 0.0


class MockImageProvider:
    """Provedor determinístico offline (não-real).

    Gera um placeholder SVG baseado no hash do prompt, sem chamada de rede.
    O ``url`` aponta para um arquivo local sob ``output/drafts/assets/``.
    Nunca é apresentado como imagem de provedor autorizado.
    """

    def __init__(self, output_dir: Path | str = Path("output/drafts/assets")):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.name = "mock"

    def generate(self, prompt: str, model: str,
                 size: str = "1080x1920") -> dict[str, Any]:
        content_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        file_name = f"mock_{content_hash}.svg"
        file_path = self.output_dir / file_name
        if not file_path.exists():
            svg = (
                '<svg xmlns="http://www.w3.org/2000/svg" '
                f'width="{size}" height="{size}">'
                f'<text x="50%" y="50%" text-anchor="middle" '
                'font-family="sans-serif" font-size="48" fill="#666">'
                f'Mock asset: {prompt[:40]}...</text></svg>'
            )
            file_path.write_text(svg, encoding="utf-8")
        size_bytes = file_path.stat().st_size
        w, h = size.split("x")
        return {
            "url": str(file_path),
            "model": model,
            "width": int(w),
            "height": int(h),
            "size_bytes": size_bytes,
            "format": "svg",
            "cost_usd": 0.0,
        }


class SceneAssetManager:
    """Gerencia o ciclo de vida de assets visuais por cena (CP23).

    Responsabilidades:
    - Derivar prompts visuais do roteiro (VisualPrompt).
    - Chamar um ``ImageProvider`` configurável para gerar o asset.
    - Cache de imagem por hash de conteúdo (reuso de recursos existentes).
    - Associar cena, prompt, modelo e asset (SceneAsset).
    - Registrar proveniência, licença e custo (AssetProvenance).
    - Manter identidade visual consistente de personagens (CharacterIdentity).
    - Fallback seguro quando o provedor falha (asset de fallback/erro).
    """

    def __init__(self, provider: ImageProvider) -> None:
        self.provider = provider
        self.is_mock = provider.__class__.__name__ == "MockImageProvider"
        self._cache_by_hash: dict[str, str] = {}  # content_hash -> asset_id

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _content_hash(prompt: str, model: str) -> str:
        return hashlib.sha256(
            f"{model}|{prompt}".encode("utf-8")
        ).hexdigest()[:16]

    def _find_character(self, state: dict[str, Any],
                        name: str) -> Optional[CharacterIdentity]:
        for ch in state.get("character_identities", []) or []:
            if ch.get("name") == name:
                return CharacterIdentity.model_validate(ch)
        return None

    def _register_character(self, state: dict[str, Any],
                            character: CharacterIdentity) -> None:
        chars = list(state.get("character_identities", []) or [])
        chars.append(character.model_dump(mode="json"))
        state["character_identities"] = chars

    # ------------------------------------------------------------------ API pública
    def build_visual_prompts(self, state: dict[str, Any]) -> list[VisualPrompt]:
        """Deriva prompts visuais do roteiro (uma por cena não-CTA)."""
        script = state.get("script") or {}
        scenes = script.get("scenes", []) if isinstance(script, dict) else []
        topic = state.get("topic") or ""
        style = state.get("visual_style", "vertical_1080x1920")
        model = state.get("image_model", "mock-image-v1")

        prompts: list[VisualPrompt] = []
        for idx, scene in enumerate(scenes):
            if not isinstance(scene, dict):
                continue
            role = scene.get("role", "corpo")
            heading = scene.get("heading", "") or ""
            narration = scene.get("narration", []) or []
            narration_text = " ".join(narration) if isinstance(narration, list) else str(narration)

            # Prompt visual derivado do roteiro: papel + heading + narração + estilo.
            prompt_text = (
                f"{role.upper()} cena de '{topic}'. "
                f"{heading}. {narration_text} "
                f"Estilo visual: {style}. Vídeo vertical 9:16, 1080x1920, "
                "iluminação consistente, identidade visual de marca."
            )

            character_ids: list[str] = []
            # Se houver personagem definido no estado, associa à cena.
            chars = state.get("character_identities") or []
            if chars:
                character_ids = [c.get("character_id") or c.get("id", "")
                                 for c in chars if isinstance(c, dict)]

            prompts.append(
                VisualPrompt(
                    scene_id=state.get("video_id", ""),
                    scene_role=role,
                    scene_index=idx,
                    prompt_text=prompt_text,
                    style=style,
                    model=model,
                    character_ids=character_ids,
                    negative_prompt="desfocado, distorcido, baixa resolução, "
                    "texto estranho, identidade inconsistente",
                )
            )

        existing = state.get("visual_prompts") or []
        existing_ids = {p.get("prompt_id") for p in existing if isinstance(p, dict)}
        new_dump = [p.model_dump(mode="json") for p in prompts
                    if p.prompt_id not in existing_ids]
        state["visual_prompts"] = existing + new_dump
        return prompts

    def generate_assets(self, state: dict[str, Any]) -> list[SceneAsset]:
        """Gera (ou reaproveita via cache) os assets visuais das cenas."""
        prompts = state.get("visual_prompts") or []
        if isinstance(prompts, list):
            prompts = [VisualPrompt.model_validate(p) if not isinstance(p, SceneAsset) else p
                       for p in prompts]
        else:
            prompts = []

        scene_assets: list[SceneAsset] = []
        existing_assets = state.get("scene_assets") or []
        existing_by_scene: dict[str, SceneAsset] = {}
        for a in existing_assets:
            if isinstance(a, dict):
                try:
                    existing_by_scene[a.get("scene_index", "")] = SceneAsset.model_validate(a)
                except Exception:
                    continue

        model = state.get("image_model", "mock-image-v1")
        size = state.get("image_size", "1080x1920")

        for prompt in prompts:
            if not isinstance(prompt, VisualPrompt):
                prompt = VisualPrompt.model_validate(prompt)

            # Reaproveita asset já gerado para esta cena.
            existing = existing_by_scene.get(str(prompt.scene_index))
            if existing and existing.url:
                scene_assets.append(existing)
                continue

            # Cache por hash de conteúdo: reaproveita asset idêntico.
            content_hash = self._content_hash(prompt.prompt_text, prompt.model)
            if content_hash in self._cache_by_hash:
                reused_id = self._cache_by_hash[content_hash]
                reused = next((a for a in scene_assets if a.asset_id == reused_id), None)
                if reused is None:
                    # Procura nos assets existentes do estado.
                    for a in existing_assets:
                        if isinstance(a, dict) and a.get("asset_id") == reused_id:
                            reused = SceneAsset.model_validate(a)
                            break
                if reused:
                    asset = SceneAsset.model_validate(reused.model_dump(mode="json"))
                    asset.asset_id = hashlib.sha256(
                        f"{asset.asset_id}|reuse|{prompt.scene_index}".encode()
                    ).hexdigest()[:12]
                    asset.reused_from = reused.asset_id
                    asset.scene_id = state.get("video_id", "")
                    asset.scene_role = prompt.scene_role
                    asset.scene_index = prompt.scene_index
                    asset.prompt = prompt.prompt_text
                    asset.model = prompt.model
                    asset.content_hash = content_hash
                    # Atualiza hit count no cache.
                    cache = state.get("asset_cache") or []
                    for c in cache:
                        if isinstance(c, dict) and c.get("content_hash") == content_hash:
                            c["hit_count"] = int(c.get("hit_count", 0)) + 1
                            c["last_accessed"] = _utc_now_iso()
                            break
                    scene_assets.append(asset)
                    continue

            # Gera novo asset via provedor.
            try:
                generated = self.provider.generate(
                    prompt.prompt_text, prompt.model, size=size
                )
            except Exception as exc:  # noqa: BLE001 - fallback seguro
                # Fallback: registra asset com erro, não quebra o pipeline.
                asset = SceneAsset(
                    scene_id=state.get("video_id", ""),
                    scene_role=prompt.scene_role,
                    scene_index=prompt.scene_index,
                    prompt=prompt.prompt_text,
                    model=model,
                    url="",
                    content_hash=content_hash,
                    provenance=AssetProvenance(
                        provider=self.provider.name,
                        model=model,
                        prompt=prompt.prompt_text,
                        license_type="unknown",
                        cost_usd=0.0,
                        verified=False,
                    ),
                )
                # Marca como falha no campo url vazio; o pipeline deve tratar.
                scene_assets.append(asset)
                continue

            file_path = Path(generated["url"])
            file_hash = ""
            if file_path.exists():
                file_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()[:16]

            provenance = AssetProvenance(
                asset_id="",
                provider=self.provider.name,
                model=generated["model"],
                prompt=prompt.prompt_text,
                license_type="unknown" if self.is_mock else "review_required",
                license_url=None,
                cost_usd=float(generated.get("cost_usd", 0.0)),
                verified=not self.is_mock,
            )

            asset = SceneAsset(
                scene_id=state.get("video_id", ""),
                scene_role=prompt.scene_role,
                scene_index=prompt.scene_index,
                prompt=prompt.prompt_text,
                model=generated["model"],
                url=generated["url"],
                file_hash=file_hash,
                content_hash=content_hash,
                width=generated.get("width"),
                height=generated.get("height"),
                format=generated.get("format", "png"),
                size_bytes=int(generated.get("size_bytes", 0)),
                provenance=provenance,
                character_id=prompt.character_ids[0] if prompt.character_ids else None,
            )

            # Registra no cache por conteúdo.
            self._cache_by_hash[content_hash] = asset.asset_id
            cache = list(state.get("asset_cache") or [])
            cache.append(
                AssetCacheEntry(
                    content_hash=content_hash,
                    asset_id=asset.asset_id,
                    url=asset.url,
                ).model_dump(mode="json")
            )
            state["asset_cache"] = cache

            scene_assets.append(asset)

        # Persiste assets no estado (merge com existentes).
        existing_dump = [a.model_dump(mode="json") if isinstance(a, SceneAsset) else a
                         for a in existing_assets]
        new_dump = [a.model_dump(mode="json") for a in scene_assets]
        state["scene_assets"] = existing_dump + new_dump
        return scene_assets

    def attach_character(self, state: dict[str, Any],
                         name: str, description: str,
                         visual_traits: Sequence[str],
                         reference_asset_id: Optional[str] = None) -> CharacterIdentity:
        """Registra/mantém a identidade visual consistente de um personagem."""
        existing = self._find_character(state, name)
        if existing:
            return existing
        character = CharacterIdentity(
            name=name,
            description=description,
            visual_traits=list(visual_traits),
            reference_asset_id=reference_asset_id,
            consistency_hash=hashlib.sha256(
                f"{name}|{'|'.join(visual_traits)}".encode("utf-8")
            ).hexdigest()[:16],
        )
        self._register_character(state, character)
        return character


__all__ = [
    "ImageProvider",
    "MockImageProvider",
    "GeneratedImage",
    "SceneAssetManager",
]
