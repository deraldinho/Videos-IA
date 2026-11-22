"""Testes unitários para src.scripts.

Executa com: pytest (raiz em tiktok-automation).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from src.errors import DuplicateError, PipelineError  # noqa: E402
from src.ideas import build_prompt  # noqa: E402
from src.scripts import (  # noqa: E402
    KINDS,
    ROLES,
    Source,
    Scene,
    Script,
    TEMPLATES,
    load_script,
    normalize_topic,
    read_catalog,
    script_fingerprint,
    similar_topic,
)

CATALOG_PATH = ROOT / "config" / "sources.yaml"
DEMO_PATH = ROOT / "examples" / "demo-15s.json"


# --------------------------------------------------------------------------- #
# Catálogo
# --------------------------------------------------------------------------- #
def test_read_catalog_loads_sources():
    catalog = read_catalog(CATALOG_PATH)
    assert isinstance(catalog, dict)
    assert "cern-first-website" in catalog
    src = catalog["cern-first-website"]
    assert isinstance(src, Source)
    assert src.id == "cern-first-website"
    assert src.url.startswith("https://")
    assert src.editorial_evidence


def test_read_catalog_rejects_http_not_https(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "insegura:\n  title: T\n  url: http://example.com\n  publisher: P\n  accessed_at: 2026-01-01\n  editorial_evidence: x\n",
        encoding="utf-8",
    )
    with pytest.raises(PipelineError, match="HTTPS"):
        read_catalog(bad)


def test_read_catalog_rejects_missing_editorial_evidence(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "sem_evidencia:\n  title: T\n  url: https://example.com\n  publisher: P\n  accessed_at: 2026-01-01\n",
        encoding="utf-8",
    )
    with pytest.raises(PipelineError, match="editorial_evidence"):
        read_catalog(bad)


def test_read_catalog_rejects_missing_file():
    with pytest.raises(PipelineError, match="não encontrado"):
        read_catalog(ROOT / "config" / "nao_existe.yaml")


def test_duplicate_error_is_pipeline_error():
    with pytest.raises(PipelineError):
        raise DuplicateError("fonte duplicada")


# --------------------------------------------------------------------------- #
# load_script — factual válido (demo de 15s)
# --------------------------------------------------------------------------- #
def _catalog():
    return read_catalog(CATALOG_PATH)


def test_load_script_demo_is_exactly_15s():
    raw = json.loads(DEMO_PATH.read_text(encoding="utf-8"))
    script = load_script(raw, _catalog(), 1.0, 60.0)
    assert script.total_seconds == 15.0
    assert script.kind == "factual"
    assert script.template == "curiosidade"
    assert script.needs_human_review is True
    assert len(script.hashtags) == 5
    assert all(h.startswith("#") for h in script.hashtags)


def test_load_script_valid_factual_resolves_sources():
    raw = {
        "topic": "primeiro site cern",
        "template": "curiosidade",
        "kind": "factual",
        "title": "Título curto",
        "description": "Descrição curta.",
        "hashtags": ["#CERN", "#Web", "#Tecnologia"],
        "scenes": [
            {
                "role": "gancho",
                "seconds": 3.0,
                "heading": "Hook",
                "narration": ["Frase do gancho."],
                "source_ids": ["cern-first-website"],
            },
            {
                "role": "corpo",
                "seconds": 4.0,
                "heading": "Corpo",
                "narration": ["Frase do corpo."],
                "source_ids": ["cern-first-website"],
            },
            {
                "role": "cta",
                "seconds": 2.0,
                "heading": "CTA",
                "narration": ["Segue aqui."],
                "source_ids": [],
            },
        ],
    }
    script = load_script(raw, _catalog(), 1.0, 60.0)
    assert script.total_seconds == 9.0
    assert len(script.sources) == 1
    assert script.sources[0].id == "cern-first-website"


def test_load_script_total_out_of_range():
    raw = {
        "topic": "x",
        "template": "curiosidade",
        "kind": "factual",
        "title": "T",
        "description": "D",
        "hashtags": ["#A", "#B", "#C"],
        "scenes": [
            {"role": "gancho", "seconds": 3.0, "heading": "H", "narration": ["n."], "source_ids": ["cern-first-website"]},
            {"role": "cta", "seconds": 3.0, "heading": "H", "narration": ["n."], "source_ids": []},
        ],
    }
    # total 6s, fora de [30, 60]
    with pytest.raises(PipelineError, match="fora do intervalo"):
        load_script(raw, _catalog(), 30.0, 60.0)


def test_load_script_factual_scene_missing_refs():
    raw = {
        "topic": "x",
        "template": "curiosidade",
        "kind": "factual",
        "title": "T",
        "description": "D",
        "hashtags": ["#A", "#B", "#C"],
        "scenes": [
            {"role": "corpo", "seconds": 5.0, "heading": "H", "narration": ["n."], "source_ids": []},
            {"role": "cta", "seconds": 2.0, "heading": "H", "narration": ["n."], "source_ids": []},
        ],
    }
    with pytest.raises(PipelineError, match="referência de fonte"):
        load_script(raw, _catalog(), 1.0, 60.0)


def test_load_script_source_id_not_in_catalog():
    raw = {
        "topic": "x",
        "template": "curiosidade",
        "kind": "factual",
        "title": "T",
        "description": "D",
        "hashtags": ["#A", "#B", "#C"],
        "scenes": [
            {"role": "gancho", "seconds": 3.0, "heading": "H", "narration": ["n."], "source_ids": ["inexistente"]},
            {"role": "cta", "seconds": 2.0, "heading": "H", "narration": ["n."], "source_ids": []},
        ],
    }
    with pytest.raises(PipelineError, match="não encontrado no catálogo"):
        load_script(raw, _catalog(), 1.0, 60.0)


# --------------------------------------------------------------------------- #
# Ficção
# --------------------------------------------------------------------------- #
def test_load_script_fiction_no_sources_and_marked_in_caption():
    raw = {
        "topic": "história imaginária",
        "template": "historia",
        "kind": "ficcao",
        "title": "Título ficção",
        "description": "Uma descrição de ficção.",
        "hashtags": ["#Ficção", "#Criativo", "#TikTok"],
        "scenes": [
            {"role": "gancho", "seconds": 3.0, "heading": "H", "narration": ["Imaginemos."], "source_ids": []},
            {"role": "corpo", "seconds": 4.0, "heading": "H", "narration": ["Era uma vez."], "source_ids": []},
            {"role": "cta", "seconds": 3.0, "heading": "H", "narration": ["Gostou?"], "source_ids": []},
        ],
    }
    script = load_script(raw, _catalog(), 1.0, 60.0)
    assert script.kind == "ficcao"
    assert script.sources == ()
    assert script.caption.startswith("[Ficção]")


def test_load_script_fiction_with_source_is_rejected():
    raw = {
        "topic": "x",
        "template": "historia",
        "kind": "ficcao",
        "title": "T",
        "description": "D",
        "hashtags": ["#A", "#B", "#C"],
        "scenes": [
            {"role": "gancho", "seconds": 3.0, "heading": "H", "narration": ["n."], "source_ids": ["cern-first-website"]},
            {"role": "cta", "seconds": 2.0, "heading": "H", "narration": ["n."], "source_ids": []},
        ],
    }
    with pytest.raises(PipelineError, match="ficção não deve referenciar fontes"):
        load_script(raw, _catalog(), 1.0, 60.0)


# --------------------------------------------------------------------------- #
# Chaves extras / estrutura
# --------------------------------------------------------------------------- #
def test_load_script_rejects_extra_keys():
    raw = {
        "topic": "x",
        "template": "curiosidade",
        "kind": "factual",
        "title": "T",
        "description": "D",
        "hashtags": ["#A", "#B", "#C"],
        "scenes": [
            {"role": "cta", "seconds": 2.0, "heading": "H", "narration": ["n."], "source_ids": []}
        ],
        "chave_inexistente": 123,
    }
    with pytest.raises(PipelineError, match="não permitidas"):
        load_script(raw, _catalog(), 1.0, 60.0)


def test_load_script_rejects_extra_scene_keys():
    raw = {
        "topic": "x",
        "template": "curiosidade",
        "kind": "factual",
        "title": "T",
        "description": "D",
        "hashtags": ["#A", "#B", "#C"],
        "scenes": [
            {
                "role": "cta",
                "seconds": 2.0,
                "heading": "H",
                "narration": ["n."],
                "source_ids": [],
                "campo_extra": True,
            }
        ],
    }
    with pytest.raises(PipelineError, match="não permitidas na cena"):
        load_script(raw, _catalog(), 1.0, 60.0)


def test_load_script_rejects_non_dict_raw():
    with pytest.raises(PipelineError, match="objeto JSON"):
        load_script([1, 2, 3], _catalog(), 1.0, 60.0)


def test_hook_max_3_seconds():
    raw = {
        "topic": "x",
        "template": "curiosidade",
        "kind": "factual",
        "title": "T",
        "description": "D",
        "hashtags": ["#A", "#B", "#C"],
        "scenes": [
            {"role": "gancho", "seconds": 4.0, "heading": "H", "narration": ["n."], "source_ids": ["cern-first-website"]},
            {"role": "cta", "seconds": 2.0, "heading": "H", "narration": ["n."], "source_ids": []},
        ],
    }
    with pytest.raises(PipelineError, match="no máximo 3s"):
        load_script(raw, _catalog(), 1.0, 60.0)


def test_hashtags_out_of_range_rejected():
    base = {
        "topic": "x",
        "template": "curiosidade",
        "kind": "factual",
        "title": "T",
        "description": "D",
        "scenes": [
            {"role": "gancho", "seconds": 2.0, "heading": "H", "narration": ["n."], "source_ids": ["cern-first-website"]},
            {"role": "cta", "seconds": 2.0, "heading": "H", "narration": ["n."], "source_ids": []},
        ],
    }
    raw2 = dict(base, hashtags=["#A", "#B"])
    with pytest.raises(PipelineError, match="entre 3 e 5"):
        load_script(raw2, _catalog(), 1.0, 60.0)
    raw6 = dict(base, hashtags=["#A", "#B", "#C", "#D", "#E", "#F"])
    with pytest.raises(PipelineError, match="entre 3 e 5"):
        load_script(raw6, _catalog(), 1.0, 60.0)


# --------------------------------------------------------------------------- #
# as_dict / caption / total_seconds
# --------------------------------------------------------------------------- #
def test_as_dict_json_ready_includes_sources():
    raw = {
        "topic": "primeiro site cern",
        "template": "curiosidade",
        "kind": "factual",
        "title": "Título",
        "description": "Descrição.",
        "hashtags": ["#CERN", "#Web", "#Tecnologia"],
        "scenes": [
            {"role": "gancho", "seconds": 3.0, "heading": "H", "narration": ["Frase."], "source_ids": ["cern-first-website"]},
            {"role": "cta", "seconds": 2.0, "heading": "H", "narration": ["Frase."], "source_ids": []},
        ],
    }
    script = load_script(raw, _catalog(), 1.0, 60.0)
    data = script.as_dict()
    assert json.dumps(data, ensure_ascii=False)  # serializável
    assert data["total_seconds"] == 5.0
    assert len(data["sources"]) == 1
    assert data["sources"][0]["id"] == "cern-first-website"
    assert data["sources"][0]["url"].startswith("https://")


def test_caption_bounded_2200():
    raw = {
        "topic": "x",
        "template": "curiosidade",
        "kind": "factual",
        "title": "T",
        "description": "Uma descricao curta para teste de legenda.",
        "hashtags": ["#A", "#B", "#C"],
        "scenes": [
            {"role": "gancho", "seconds": 2.0, "heading": "H", "narration": ["n."], "source_ids": ["cern-first-website"]},
            {"role": "cta", "seconds": 2.0, "heading": "H", "narration": ["n."], "source_ids": []},
        ],
    }
    script = load_script(raw, _catalog(), 1.0, 60.0)
    assert len(script.caption) <= 2200
    assert all(tag in script.caption for tag in script.hashtags)


def test_round_trip_ignores_supplied_sources_snapshot():
    raw = {
        "topic": "primeiro site cern",
        "template": "curiosidade",
        "kind": "factual",
        "title": "Título",
        "description": "Descrição.",
        "hashtags": ["#CERN", "#Web", "#Tecnologia"],
        "scenes": [
            {"role": "cta", "seconds": 4.0, "heading": "H", "narration": ["Frase."], "source_ids": []}
        ],
        "sources": [{"id": "fonte-falsa", "title": "Falso", "url": "https://falso.com", "publisher": "F", "accessed_at": "2026-01-01", "editorial_evidence": "x"}],
    }
    script = load_script(raw, _catalog(), 1.0, 60.0)
    # o snapshot falso não é confiado; fontes vêm do catálogo via source_ids das cenas
    assert script.sources == ()  # cena cta sem source_ids


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def test_normalize_topic():
    assert normalize_topic("  Olá, Mundo!  ") == "ola mundo"
    assert normalize_topic("CERN   e a Web") == "cern e a web"


def test_script_fingerprint_deterministic():
    raw = {
        "topic": "x",
        "template": "curiosidade",
        "kind": "factual",
        "title": "T",
        "description": "D",
        "hashtags": ["#A", "#B", "#C"],
        "scenes": [
            {"role": "cta", "seconds": 2.0, "heading": "H", "narration": ["Falando agora."], "source_ids": []}
        ],
    }
    script = load_script(raw, _catalog(), 1.0, 60.0)
    fp1 = script_fingerprint(script)
    fp2 = script_fingerprint(script)
    assert fp1 == fp2
    assert len(fp1) == 64  # SHA-256 hex


def test_similar_topic_conservative():
    assert similar_topic("Primeiro site do CERN", "primeiro site do cern") is True
    assert similar_topic("CERN e a web", "Inteligência artificial") is False
    assert similar_topic("", "algo") is False


def test_constants_exported():
    assert "factual" in KINDS and "ficcao" in KINDS
    assert "gancho" in ROLES and "cta" in ROLES
    assert "curiosidade" in TEMPLATES


# --------------------------------------------------------------------------- #
# build_prompt
# --------------------------------------------------------------------------- #
def test_build_prompt_returns_nonempty_ptbr():
    catalog = _catalog()
    prompt = build_prompt(
        niche="curiosidades de tecnologia",
        kind="factual",
        target_seconds=45,
        min_seconds=30,
        max_seconds=60,
        catalog=catalog,
        previous_topics=[],
    )
    assert isinstance(prompt, str) and len(prompt) > 100
    assert "JSON" in prompt
    assert "HTTPS" in prompt
    assert "cern-first-website" in prompt
    assert "NÃO afirme a veracidade automática" in prompt or "veracidade" in prompt
