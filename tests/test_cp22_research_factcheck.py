"""Testes TDD para CP22 — Research Intelligence + FactCheck real.

Critérios de aceitação (missão CP22):
- Pesquisa armazena URLs, datas, autores e evidências (ResearchSource).
- Pontuação de relevância, originalidade e confiabilidade (0-1).
- Detecção de tópicos repetidos (DuplicateTopic via topic_hash sha256).
- Nunca apresenta fontes simuladas como reais (evidence_level "none" para mock,
  URLs https://exemplo.com, editorial_evidence declara "simulado").
- Fact-check detecta afirmações não verificadas.
- Bloqueia conteúdo factual não verificado (veredito PENDING/REJECTED com blocking=True).
- Fontes mock nunca constituem evidência real.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.workflow.agents.factcheck import FactCheckAgent
from src.workflow.agents.research import ResearchAgent
from src.workflow.research_connectors import FactCheckEngine, ResearchIntelligence
from src.workflow.state import (
    ClaimStatus,
    FactCheckClaim,
    FactCheckVerdict,
    ResearchResult,
    ResearchSource,
    empty_state,
    state_to_dict,
)


# --------------------------------------------------------------------------- #
# Conector auxiliar que simula uma fonte real (não-mock)                      #
# --------------------------------------------------------------------------- #
class _RealLikeConnector:
    """Conector de teste que retorna fontes não-simuladas (verdadeiras)."""

    name = "real"

    def search(self, topic: str) -> list[dict[str, Any]]:
        return [
            {
                "title": f"Evidência verificada sobre {topic}",
                "url": "https://verdadeiro.com.br/fonte",
                "publisher": "Verdadeiro News",
                "editorial_evidence": "Reportagem verificada com dados oficiais e "
                "fontes primárias.",
                "relevance": 0.9,
                "reliability": 0.9,
            }
        ]


# --------------------------------------------------------------------------- #
# Modelos de estado (CP22)                                                    #
# --------------------------------------------------------------------------- #
def test_research_source_stores_url_date_author_evidence():
    src = ResearchSource(
        title="Estudo sobre IA",
        url="https://verdadeiro.com.br/ia",
        publisher="Verdadeiro News",
        editorial_evidence="Dados de pesquisa primária.",
        relevance_score=0.8,
        reliability_score=0.7,
    )
    assert src.url == "https://verdadeiro.com.br/ia"
    assert src.publisher == "Verdadeiro News"
    assert src.accessed_at  # data preenchida automaticamente
    assert src.editorial_evidence == "Dados de pesquisa primária."
    assert 0.0 <= src.relevance_score <= 1.0
    assert 0.0 <= src.reliability_score <= 1.0


def test_research_result_defaults():
    result = ResearchResult(topic="curiosidade tech")
    assert result.topic == "curiosidade tech"
    assert result.sources == []
    assert result.total_sources == 0
    assert result.research_method == "mock"
    assert result.evidence_level == "none"
    assert result.generated_at


def test_claim_status_and_verdict_enums():
    assert ClaimStatus.UNVERIFIED == "unverified"
    assert ClaimStatus.VERIFIED == "verified"
    assert ClaimStatus.REFUTED == "refuted"
    assert FactCheckVerdict.OK == "ok"
    assert FactCheckVerdict.PENDING == "pending"
    assert FactCheckVerdict.REJECTED == "rejected"


def test_workflow_state_carry_cp22_fields():
    """WorkflowState aceita os novos campos CP22 e faz round-trip por dict."""
    state = empty_state("exec-1", "v1", topic="t")
    state.research_result = ResearchResult(
        topic="t", evidence_level="none", sources=[]
    )
    data = state_to_dict(state)
    assert data["research_result"]["topic"] == "t"
    assert data["research_result"]["evidence_level"] == "none"
    restored = ResearchResult.model_validate(data["research_result"])
    assert restored.topic == "t"


# --------------------------------------------------------------------------- #
# MockResearchConnector — nunca apresenta simulado como real                  #
# --------------------------------------------------------------------------- #
def test_mock_connector_returns_exemplo_urls_nao_reais():
    from src.workflow.research_connectors import MockResearchConnector

    conn = MockResearchConnector()
    sources = conn.search("curiosidade tech")
    assert len(sources) == 2
    for s in sources:
        assert s["url"].startswith("https://exemplo.com/")
        assert "simulado" in s["publisher"].lower()
        assert "fonte verificável real" in s["editorial_evidence"].lower()


def _research_state(topic: str) -> dict[str, Any]:
    return state_to_dict(empty_state("exec-r", "vid-r", topic=topic))


# --------------------------------------------------------------------------- #
# ResearchIntelligence — pontuação e evidência                                #
# --------------------------------------------------------------------------- #
def test_research_intelligence_mock_evidence_level_none():
    from src.workflow.research_connectors import MockResearchConnector

    intel = ResearchIntelligence(MockResearchConnector())
    assert intel.is_mock is True
    result = intel.research("curiosidade tech")
    assert result.evidence_level == "none"
    assert result.research_method == "MockResearchConnector"
    assert result.total_sources == 2
    for src in result.sources:
        # Fonte simulada nunca é verificada.
        assert src.reliability_score <= 0.35
        assert src.content_hash


def test_research_source_has_accessed_date_and_evidence():
    from src.workflow.research_connectors import MockResearchConnector

    intel = ResearchIntelligence(MockResearchConnector())
    result = intel.research("IA na educação")
    assert len(result.sources) == 2
    for src in result.sources:
        assert src.url
        assert src.publisher
        assert src.accessed_at
        assert src.editorial_evidence
        # A evidência declara explicitamente que não é uma fonte verificável real.
        assert "fonte verificável real" in src.editorial_evidence.lower()


# --------------------------------------------------------------------------- #
# Detecção de tópicos repetidos                                               #
# --------------------------------------------------------------------------- #
def test_topic_hash_is_deterministic():
    h1 = ResearchIntelligence._topic_hash("Curiosidade Tech")
    h2 = ResearchIntelligence._topic_hash("curiosidade tech")
    h3 = ResearchIntelligence._topic_hash("outro tema")
    assert h1 == h2  # case-insensitive
    assert len(h1) == 16
    assert h1 != h3


def test_duplicate_topic_record():
    from src.workflow.research_connectors import MockResearchConnector
    from src.workflow.state import DuplicateTopic

    intel = ResearchIntelligence(MockResearchConnector())
    dt = intel.build_duplicate_topic("curiosidade tech")
    assert isinstance(dt, DuplicateTopic)
    assert dt.topic == "curiosidade tech"
    assert dt.topic_hash == ResearchIntelligence._topic_hash("curiosidade tech")
    assert dt.occurrence_count == 1
    assert dt.first_seen


def test_research_agent_tracks_duplicate_topics():
    state = _research_state("curiosidade tech")
    out = ResearchAgent()(state)
    assert out["duplicate_topics"]
    assert out["duplicate_topics"][0]["topic"] == "curiosidade tech"
    assert out["duplicate_topics"][0]["occurrence_count"] == 1

    # Repetir o mesmo tópico incrementa occurrence_count.
    out2 = ResearchAgent()(out)
    assert out2["duplicate_topics"][0]["occurrence_count"] == 2


def test_research_agent_stores_typed_result():
    state = _research_state("IA na educação")
    out = ResearchAgent()(state)
    assert out["research_result"] is not None
    assert out["research_result"]["topic"] == "IA na educação"
    assert out["research_result"]["evidence_level"] == "none"
    assert out["research_result"]["research_method"] == "MockResearchConnector"
    assert out["research_sources"]  # dicts compatíveis com estado legado
    assert out["status"] == "IDEA_RESEARCHED"
    assert out["current_stage"] == "IDEA_RESEARCHED"


# --------------------------------------------------------------------------- #
# FactCheckEngine — detecção de não-verificado e bloqueio                     #
# --------------------------------------------------------------------------- #
def _fc_state(script: dict[str, Any], research: ResearchResult) -> dict[str, Any]:
    base = state_to_dict(empty_state("exec-fc", "vid-fc", topic="tema"))
    base["script"] = script
    base["research_result"] = research.model_dump(mode="json")
    return base


def test_factcheck_mock_keeps_claims_unverified():
    from src.workflow.research_connectors import MockResearchConnector

    engine = FactCheckEngine(MockResearchConnector())
    assert engine.is_mock is True
    script = {
        "title": "T",
        "scenes": [
            {"role": "gancho", "narration": ["A inteligência artificial cresce."]},
        ],
    }
    research = ResearchResult(topic="IA", evidence_level="none", sources=[])
    result = engine.run(script, research)
    assert result.verdict == FactCheckVerdict.PENDING
    assert result.blocking is True  # todas as afirmações não verificadas
    for claim in result.claims:
        assert claim.status == ClaimStatus.UNVERIFIED
        assert claim.confidence_score == 0.0
        assert claim.evidence_urls == []


def test_factcheck_mock_never_treats_simulated_as_evidence():
    from src.workflow.research_connectors import MockResearchConnector

    engine = FactCheckEngine(MockResearchConnector())
    assert engine.is_mock is True
    # Fonte simulada (mesmo com overlap de tokens) não vira evidência real.
    fake = ResearchSource(
        title="Energia solar cresce no Brasil",
        url="https://exemplo.com/fake",
        publisher="exemplo.com (simulado)",
        editorial_evidence="simulado",
    )
    claim = engine._verify_claim(
        FactCheckClaim(claim_text="energia solar cresce no brasil"), [fake]
    )
    assert claim.status == ClaimStatus.UNVERIFIED
    assert claim.evidence_urls == []


def test_factcheck_real_connector_verified_claims_ok():
    engine = FactCheckEngine(_RealLikeConnector())
    assert engine.is_mock is False
    script = {
        "scenes": [
            {"role": "gancho", "narration": ["energia solar cresce no brasil"]},
        ]
    }
    research = ResearchResult(
        topic="energia",
        evidence_level="partial",
        sources=[
            ResearchSource(
                title="Evidência verificada sobre energia",
                url="https://verdadeiro.com.br/fonte",
                publisher="Verdadeiro News",
                editorial_evidence="Reportagem verificada com dados oficiais.",
                relevance_score=0.9,
                reliability_score=0.9,
            )
        ],
    )
    result = engine.run(script, research)
    assert result.verdict == FactCheckVerdict.OK
    assert result.blocking is False
    assert result.verified_claims == 1
    assert result.pending_claims == 0


def test_factcheck_real_connector_no_sources_pending_blocked():
    engine = FactCheckEngine(_RealLikeConnector())
    script = {"scenes": [{"role": "gancho", "narration": ["qualquer afirmação"]}]}
    research = ResearchResult(topic="x", evidence_level="none", sources=[])
    result = engine.run(script, research)
    assert result.verdict == FactCheckVerdict.PENDING
    assert result.blocking is True


def test_factcheck_rejects_when_all_claims_unverified_real():
    engine = FactCheckEngine(_RealLikeConnector())
    script = {
        "scenes": [
            {"role": "gancho", "narration": ["afirmação totalmente sem relação"]},
        ]
    }
    research = ResearchResult(
        topic="x",
        evidence_level="partial",
        sources=[
            ResearchSource(
                title="Título completamente diferente",
                url="https://verdadeiro.com.br/outro",
                publisher="Verdadeiro News",
                editorial_evidence="outro assunto",
            )
        ],
    )
    result = engine.run(script, research)
    assert result.verdict == FactCheckVerdict.REJECTED
    assert result.blocking is True
    assert result.verified_claims == 0
    assert result.pending_claims == 1


# --------------------------------------------------------------------------- #
# FactCheckAgent — bloqueio no estado                                         #
# --------------------------------------------------------------------------- #
def test_factcheck_agent_blocks_unverified_factual_content():
    state = _fc_state(
        {
            "title": "T",
            "scenes": [
                {"role": "gancho", "narration": ["energia solar cresce no brasil"]},
                {"role": "cta", "narration": ["assine agora"]},
            ],
        },
        ResearchResult(topic="tema", evidence_level="none", sources=[]),
    )
    out = FactCheckAgent()(state)
    assert out["factcheck_result"]["verdict"] == "pending"
    assert out["factcheck_result"]["blocking"] is True
    assert out["status"] == "FACTCHECKED"
    assert out["current_stage"] == "FACTCHECKED"


def test_factcheck_agent_stores_typed_result():
    state = _fc_state(
        {"title": "T", "scenes": [{"role": "gancho", "narration": ["oi"]}]},
        ResearchResult(topic="tema", evidence_level="none", sources=[]),
    )
    out = FactCheckAgent()(state)
    assert out["factcheck"] is not None
    assert out["factcheck_result"] is not None
    assert out["factcheck_result"]["checked_claims"] >= 1


__all__ = []
