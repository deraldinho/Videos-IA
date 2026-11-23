"""Testes TDD para CP23 — Scene Asset Intelligence.

Critérios de aceitação (missão CP23):
- Prompts visuais derivados do roteiro (uma por cena).
- Provedor de imagem configurável (mock em modo offline).
- Personagens e identidade visual consistentes (CharacterIdentity).
- Cache de imagens por hash de conteúdo (reuso de recursos existentes).
- Associação cena → prompt → modelo → asset (SceneAsset).
- Registro de proveniência, licença e custo (AssetProvenance).
- Fallback seguro quando o provedor falha (não quebra o pipeline).
- Nunca apresenta imagens simuladas como reais de um provedor autorizado.
- Integra à renderização atual sem substituir ArtCraft/FilmCraft/HyperFrames.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.workflow.agents.visual import VisualAgent
from src.workflow.assets import (
    GeneratedImage,
    ImageProvider,
    MockImageProvider,
    SceneAssetManager,
)
from src.workflow.state import (
    AssetCacheEntry,
    AssetProvenance,
    CharacterIdentity,
    SceneAsset,
    VisualPrompt,
    empty_state,
    state_to_dict,
)


# --------------------------------------------------------------------------- #
# Provedor auxiliar controlado para testes (não-mock)                         #
# --------------------------------------------------------------------------- #
class _ControlledProvider:
    """Provedor de teste controlável. Não é MockImageProvider."""

    name = "controlled"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = False

    def generate(self, prompt: str, model: str,
                 size: str = "1080x1920") -> dict[str, Any]:
        self.calls.append({"prompt": prompt, "model": model, "size": size})
        if self.fail:
            raise RuntimeError("provedor indisponível")
        content_hash = __import__("hashlib").sha256(
            f"{model}|{prompt}".encode("utf-8")
        ).hexdigest()[:16]
        return GeneratedImage(
            url=f"output/drafts/assets/controlled_{content_hash}.png",
            model=model,
            width=1080,
            height=1920,
            size_bytes=1234,
            format="png",
            cost_usd=0.001,
        ).__dict__


# --------------------------------------------------------------------------- #
# Modelos de estado (CP23)                                                    #
# --------------------------------------------------------------------------- #
def test_sceneasset_stores_provenance_license_cost():
    asset = SceneAsset(
        scene_id="vid-1",
        scene_role="gancho",
        scene_index=0,
        prompt="Cena de abertura",
        model="mock-image-v1",
        url="output/drafts/assets/mock_abc.svg",
        content_hash="abc123",
        width=1080,
        height=1920,
        format="svg",
        size_bytes=500,
        provenance=AssetProvenance(
            provider="mock",
            model="mock-image-v1",
            prompt="Cena de abertura",
            license_type="unknown",
            cost_usd=0.0,
            verified=False,
        ),
    )
    assert asset.asset_id
    assert asset.provenance.provider == "mock"
    assert asset.provenance.license_type == "unknown"
    assert asset.provenance.cost_usd == 0.0
    assert asset.provenance.verified is False
    assert asset.created_at


def test_character_identity_consistency():
    mgr = SceneAssetManager(MockImageProvider())
    state = _base_state()
    ch1 = mgr.attach_character(
        state, name="Maria", description="Narradora da marca",
        visual_traits=["loira", "casaco azul"],
        reference_asset_id="asset-ref-1",
    )
    # O consistency_hash é derivado do nome + traços (identidade visual).
    assert ch1.character_id
    assert ch1.consistency_hash
    assert ch1.name == "Maria"
    assert len(ch1.visual_traits) == 2


def test_visual_prompt_derives_from_scene():
    vp = VisualPrompt(
        scene_id="vid-1",
        scene_role="gancho",
        scene_index=0,
        prompt_text="GANCHO cena de 'tema'. Título. Narração. Estilo visual: ...",
        style="vertical_1080x1920",
        model="mock-image-v1",
        character_ids=["ch-1"],
        negative_prompt="desfocado, distorcido",
    )
    assert vp.prompt_id
    assert vp.scene_index == 0
    assert vp.character_ids == ["ch-1"]
    assert vp.created_at


def test_asset_cache_entry_hit_count():
    entry = AssetCacheEntry(
        content_hash="hash123",
        asset_id="asset-1",
        url="output/drafts/assets/mock_hash123.svg",
    )
    assert entry.hit_count == 0
    assert entry.created_at
    assert entry.last_accessed


# --------------------------------------------------------------------------- #
# MockImageProvider — determinístico, sem rede                                #
# --------------------------------------------------------------------------- #
def test_mock_provider_deterministic_same_prompt_same_url(tmp_path):
    out_dir = tmp_path / "assets"
    p1 = MockImageProvider(output_dir=out_dir)
    p2 = MockImageProvider(output_dir=out_dir)
    r1 = p1.generate("prompt teste", "mock-image-v1", size="1080x1920")
    r2 = p2.generate("prompt teste", "mock-image-v1", size="1080x1920")
    assert r1["url"] == r2["url"]
    assert r1["model"] == "mock-image-v1"
    assert r1["width"] == 1080 and r1["height"] == 1920
    assert r1["format"] == "svg"
    assert Path(r1["url"]).exists()


def test_mock_provider_different_prompt_different_hash():
    out_dir = Path("/tmp/mock_provider_test")  # não escreve realmente
    p = MockImageProvider(output_dir=out_dir)
    r1 = p.generate("prompt A", "mock-image-v1")
    r2 = p.generate("prompt B", "mock-image-v1")
    assert r1["url"] != r2["url"]


# --------------------------------------------------------------------------- #
# SceneAssetManager — prompts, cache, reuse, fallback, character              #
# --------------------------------------------------------------------------- #
def _base_state(topic: str = "curiosidade tech") -> dict:
    return state_to_dict(
        empty_state("exec-23", "vid-23", topic=topic)
    ) | {
        "script": {
            "title": "T",
            "total_seconds": 15.0,
            "scenes": [
                {"role": "gancho", "seconds": 5.0, "heading": "H1",
                 "narration": ["Narração da cena um."], "source_ids": []},
                {"role": "corpo", "seconds": 5.0, "heading": "H2",
                 "narration": ["Narração da cena dois."], "source_ids": []},
            ],
        }
    }


def test_build_visual_prompts_one_per_scene():
    mgr = SceneAssetManager(MockImageProvider())
    state = _base_state()
    prompts = mgr.build_visual_prompts(state)
    assert len(prompts) == 2
    assert prompts[0].scene_index == 0
    assert prompts[0].scene_role == "gancho"
    assert prompts[1].scene_index == 1
    assert prompts[1].scene_role == "corpo"
    for p in prompts:
        assert p.prompt_text
        assert p.style == "vertical_1080x1920"
        assert p.model == "mock-image-v1"
    # Persistidos no estado.
    assert len(state["visual_prompts"]) == 2


def test_generate_assets_creates_assets_with_provenance():
    mgr = SceneAssetManager(MockImageProvider())
    state = _base_state()
    mgr.build_visual_prompts(state)
    assets = mgr.generate_assets(state)
    assert len(assets) == 2
    for a in assets:
        assert a.asset_id
        assert a.url
        assert a.content_hash
        assert a.provenance.provider == "mock"
        assert a.provenance.verified is False  # mock não verificado
        assert a.provenance.license_type == "unknown"
        assert a.scene_index in (0, 1)
    # Persistidos no estado.
    assert len(state["scene_assets"]) == 2
    assert len(state["asset_cache"]) == 2


def test_cache_by_content_hash_reuses_asset():
    mgr = SceneAssetManager(MockImageProvider())
    state = _base_state()
    mgr.build_visual_prompts(state)
    assets1 = mgr.generate_assets(state)
    assert len(state["asset_cache"]) == 2

    # Mesmo conteúdo (mesmo prompt+modelo) deve ser reaproveitado pelo cache.
    assets2 = mgr.generate_assets(state)
    # Cada entrada do cache deve ter hit_count incrementado (reuso).
    for entry in state["asset_cache"]:
        assert entry["hit_count"] >= 1
    # Não cria novos assets para o mesmo conteúdo.
    assert len(state["asset_cache"]) == 2


def test_reuse_existing_asset_for_scene():
    mgr = SceneAssetManager(MockImageProvider())
    state = _base_state()
    mgr.build_visual_prompts(state)
    assets1 = mgr.generate_assets(state)
    # Chama novamente: deve reaproveitar o asset já existente para cada cena.
    assets2 = mgr.generate_assets(state)
    assert [a.scene_index for a in assets2] == [a.scene_index for a in assets1]
    for a in assets2:
        assert a.url  # asset existente tem URL


def test_attach_character_consistent_across_scenes():
    mgr = SceneAssetManager(MockImageProvider())
    state = _base_state()
    ch1 = mgr.attach_character(
        state, name="Maria", description="Narradora",
        visual_traits=["loira", "casaco azul"],
        reference_asset_id="ref-1",
    )
    ch2 = mgr.attach_character(
        state, name="Maria", description="Narradora",
        visual_traits=["loira", "casaco azul"],
    )
    # Mesmo personagem: retorna a instância existente (identidade consistente).
    assert ch1.character_id == ch2.character_id
    assert ch1.consistency_hash == ch2.consistency_hash
    assert len(state["character_identities"]) == 1


def test_fallback_when_provider_fails_does_not_break_pipeline():
    provider = _ControlledProvider()
    provider.fail = True
    mgr = SceneAssetManager(provider)
    assert mgr.is_mock is False  # provedor controlado não é mock
    state = _base_state()
    mgr.build_visual_prompts(state)
    assets = mgr.generate_assets(state)
    # Pipeline não quebra: retorna assets com URL vazia (marcados como falha).
    assert len(assets) == 2
    for a in assets:
        assert a.url == ""
        assert a.provenance.verified is False
        assert a.provenance.license_type == "unknown"


def test_visual_agent_end_to_end_storyboard():
    state = _base_state()
    out = VisualAgent()(state)
    assert out["storyboard"] is not None
    assert out["storyboard"]["template"] == "curiosity"
    assert len(out["storyboard"]["scenes"]) == 2
    assert len(out["storyboard"]["scene_assets"]) == 2
    assert len(out["storyboard"]["visual_prompts"]) == 2
    assert out["status"] == "VISUAL_READY"
    assert out["current_stage"] == "VISUAL_READY"
    # Assets carregam asset_id no storyboard.
    for scene in out["storyboard"]["scenes"]:
        assert "asset_id" in scene


def test_visual_agent_character_attachment():
    state = _base_state()
    state["brand_character"] = {
        "name": "Maria",
        "description": "Narradora da marca",
        "visual_traits": ["loira", "casaco azul"],
    }
    out = VisualAgent()(state)
    assert out["character_identities"]
    assert out["character_identities"][0]["name"] == "Maria"


__all__ = []
