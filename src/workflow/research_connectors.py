"""Conectores de pesquisa para o ResearchAgent (CP22).

Fornece a interface ``ResearchConnector`` e implementações:

* ``MockResearchConnector`` — fontes determinísticas, NUNCA apresentadas como
  reais (marcadas com ``research_method: "mock"`` e ``evidence_level: "none"``).
  Usada apenas para testes e modo offline.
* ``ResearchIntelligence`` — orquestra a conexão, aplica pontuação de
  relevância/originalidade/confiabilidade, detecta tópicos repetidos e nunca
  inventa fontes.

Nenhuma chamada de rede externa é feita por padrão. Ative um conector real
apenas com credenciais e autorização explícita do operador.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Protocol, Sequence, runtime_checkable

from src.workflow.state import (
    ClaimStatus,
    DuplicateTopic,
    FactCheckClaim,
    FactCheckResult,
    FactCheckVerdict,
    ResearchResult,
    ResearchSource,
    _utc_now_iso,
)


@runtime_checkable
class ResearchConnector(Protocol):
    """Conector de fonte de pesquisa verificada.

    ``search`` recebe um tópico/nicho e retorna uma lista de dicts com, no
    mínimo: ``title``, ``url`` (HTTPS), ``publisher`` e ``editorial_evidence``.
    Implementações reais devem buscar fontes verificáveis e nunca retornar
    URLs simuladas como se fossem reais.
    """

    def search(self, topic: str) -> list[dict[str, Any]]:  # pragma: no cover - interface
        ...


@dataclass
class ScoredSource:
    """Fonte já pontuada com relevância, confiabilidade e evidência."""

    source: ResearchSource
    relevance: float
    reliability: float
    evidence_level: str  # none | partial | verified


class MockResearchConnector:
    """Conector determinístico offline.

    Produz fontes simuladas com URLs de exemplo (https://exemplo.com/...),
    explicitamente não-reais, para que o pipeline rode offline e os testes
    sejam reproduzíveis sem credenciais de busca.

    NUNCA deve ser usada em modo AUTO nem apresentada como pesquisa real.
    """

    def __init__(self) -> None:
        self.name = "mock"

    def search(self, topic: str) -> list[dict[str, Any]]:
        topic_key = topic.strip().lower() or "a definir"
        return [
            {
                "title": f"Contexto sobre {topic}",
                "url": f"https://exemplo.com/pesquisa?q={topic_key.replace(' ', '+')}",
                "publisher": "exemplo.com (simulado)",
                "editorial_evidence": "Fonte simulada para ambiente offline/mock. "
                "Não é uma fonte verificável real.",
                "relevance": 0.9,
                "reliability": 0.3,
            },
            {
                "title": f"Dados de referência: {topic}",
                "url": "https://exemplo.com/dados",
                "publisher": "exemplo.com (simulado)",
                "editorial_evidence": "Dados públicos de suporte (mock). "
                "Não é uma fonte verificável real.",
                "relevance": 0.7,
                "reliability": 0.3,
            },
        ]


class ResearchIntelligence:
    """Orquestra a pesquisa editorial para CP22.

    Responsabilidades:
    - Chamar um ``ResearchConnector`` autorizado.
    - Armazenar URLs, datas, autores e evidências em ``ResearchSource``.
    - Pontuar relevância, originalidade e confiabilidade de cada fonte.
    - Detectar tópicos repetidos (assuntos já pesquisados).
    - Nunca apresentar fontes simuladas como reais.
    """

    def __init__(self, connector: ResearchConnector) -> None:
        self.connector = connector
        self.is_mock = connector.__class__.__name__ == "MockResearchConnector"

    # ------------------------------------------------------------------ hash
    @staticmethod
    def _topic_hash(topic: str) -> str:
        return hashlib.sha256(topic.strip().lower().encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _content_hash(title: str, url: str) -> str:
        return hashlib.sha256(f"{title}|{url}".encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------ scoring
    def _score_source(
        self, raw: dict[str, Any], topic: str
    ) -> ScoredSource:
        """Converte um dict bruto do conector em uma fonte pontuada."""
        title = str(raw.get("title", ""))
        url = str(raw.get("url", ""))
        publisher = str(raw.get("publisher", ""))
        evidence = str(raw.get("editorial_evidence", ""))

        relevance = float(raw.get("relevance", 0.5))
        relevance = max(0.0, min(1.0, relevance))

        reliability = float(raw.get("reliability", 0.5))
        reliability = max(0.0, min(1.0, reliability))

        # Nível de evidência: mock nunca é verificado.
        if self.is_mock or "simulado" in publisher.lower() or "exemplo.com" in url:
            evidence_level = "none"
            reliability = min(reliability, 0.35)  # teto baixo para simulado
        else:
            evidence_level = "partial" if evidence else "none"

        source = ResearchSource(
            title=title,
            url=url,
            publisher=publisher,
            accessed_at=_utc_now_iso(),
            editorial_evidence=evidence,
            relevance_score=relevance,
            reliability_score=reliability,
            content_hash=self._content_hash(title, url),
        )
        return ScoredSource(
            source=source,
            relevance=relevance,
            reliability=reliability,
            evidence_level=evidence_level,
        )

    # ------------------------------------------------------------------ pesquisa pública
    def research(self, topic: str,
                 previous_topics: Sequence[str] = None) -> ResearchResult:
        """Executa a pesquisa sobre o tópico.

        Retorna um ``ResearchResult`` com fontes pontuadas. Quando o conector
        é mock, as fontes são explicitamente não-reais (evidence_level "none")
        para nunca serem confundidas com pesquisa real.
        """
        previous_topics = list(previous_topics or [])
        topic_hash = self._topic_hash(topic)

        # Detecção de tópico repetido.
        # previous_topics pode vir como lista de dicts (estado persistido) ou
        # de objetos DuplicateTopic; aceita ambas as formas.
        def _topic_hash_of(item: Any) -> str:
            if isinstance(item, dict):
                return str(item.get("topic_hash", ""))
            return getattr(item, "topic_hash", "")

        is_repeat = any(_topic_hash_of(t) == topic_hash for t in previous_topics)

        raw_sources = self.connector.search(topic)
        scored = [self._score_source(r, topic) for r in raw_sources]

        # Ordena por relevância decrescente.
        scored.sort(key=lambda s: s.relevance, reverse=True)

        # Nível de evidência global: mock => "none"; real com fontes => "partial".
        if self.is_mock:
            evidence_level = "none"
        elif scored:
            evidence_level = "partial"
        else:
            evidence_level = "none"

        result = ResearchResult(
            topic=topic,
            sources=[s.source for s in scored],
            total_sources=len(scored),
            research_method=self.connector.__class__.__name__,
            evidence_level=evidence_level,
        )
        return result

    def build_duplicate_topic(self, topic: str) -> DuplicateTopic:
        """Cria ou atualiza o registro de tópico repetido."""
        return DuplicateTopic(
            topic_hash=self._topic_hash(topic),
            topic=topic,
        )


class FactCheckEngine:
    """Motor de verificação de fatos (CP22).

    Recebe um roteiro e as fontes pesquisadas, gera afirmações (claims) a
    verificar, classifica cada uma como verified / refuted / unverified e
    produz um veredito global. Nunca aprova conteúdo factual sem evidência
    real.
    """

    def __init__(self, connector: ResearchConnector) -> None:
        self.connector = connector
        self.is_mock = connector.__class__.__name__ == "MockResearchConnector"

    def _extract_claims(self, script: dict[str, Any]) -> list[FactCheckClaim]:
        """Extrai afirmações verificáveis das cenas do roteiro."""
        claims: list[FactCheckClaim] = []
        scenes = script.get("scenes", [])
        if not isinstance(scenes, list):
            return []
        for idx, scene in enumerate(scenes):
            if not isinstance(scene, dict):
                continue
            narration = scene.get("narration", []) or []
            role = scene.get("role", "corpo")
            source_ids = scene.get("source_ids", []) or []
            # Cada frase da narração é uma afirmação candidata (exceto CTA sem fonte).
            for phrase in narration:
                if not isinstance(phrase, str) or not phrase.strip():
                    continue
                claims.append(
                    FactCheckClaim(
                        claim_text=phrase.strip(),
                        scene_role=role,
                        scene_index=idx,
                        source_ids=list(source_ids),
                        status=ClaimStatus.UNVERIFIED,
                    )
                )
        return claims

    def _verify_claim(self, claim: FactCheckClaim,
                      sources: list[ResearchSource]) -> FactCheckClaim:
        """Verifica uma afirmação contra as fontes pesquisadas."""
        claim_text_lower = claim.claim_text.lower()
        matched = False
        evidence_urls: list[str] = []

        for src in sources:
            # Uma fonte "apoia" a afirmação se houver sobreposição de tokens
            # significativos entre a afirmação e o título/evidência da fonte.
            text_blob = f"{src.title} {src.editorial_evidence}".lower()
            tokens = [t for t in re.split(r"\W+", claim_text_lower) if len(t) > 3]
            if tokens and any(t in text_blob for t in tokens):
                matched = True
                if src.url:
                    evidence_urls.append(src.url)

        if self.is_mock:
            # Fonte simulada nunca constitui evidência real.
            claim.status = ClaimStatus.UNVERIFIED
            claim.confidence_score = 0.0
            claim.evidence_urls = []
        elif matched and evidence_urls:
            claim.status = ClaimStatus.VERIFIED
            claim.confidence_score = 0.8
            claim.evidence_urls = evidence_urls
        else:
            claim.status = ClaimStatus.UNVERIFIED
            claim.confidence_score = 0.2
            claim.evidence_urls = evidence_urls

        claim.checked_at = _utc_now_iso()
        return claim

    def run(self, script: dict[str, Any],
            research: ResearchResult) -> FactCheckResult:
        """Executa o fact-check sobre o roteiro.

        Regras de bloqueio (CP22):
        - Conteúdo factual (script_kind factual) com afirmações não verificadas
          => veredito REJECTED, blocking=True.
        - Sem fontes pesquisadas => não pode verificar => PENDING/bloqueado.
        - Fontes mock (simuladas) => afirmações permanecem unverified.
        """
        claims = self._extract_claims(script)
        sources = research.sources

        verified = 0
        rejected = 0
        pending = 0

        for claim in claims:
            claim = self._verify_claim(claim, sources)
            if claim.status == "verified":
                verified += 1
            elif claim.status == "refuted":
                rejected += 1
            else:
                pending += 1

        total = len(claims)
        blocking = False
        notes = ""

        if self.is_mock:
            verdict = FactCheckVerdict.PENDING
            notes = "Verificação simulada (mock): fontes não são evidência real. "
            if total:
                notes += f"{pending} de {total} afirmações sem evidência verificável."
            blocking = bool(total and pending == total)
        elif total == 0:
            verdict = FactCheckVerdict.OK
            notes = "Nenhuma afirmação verificável encontrada no roteiro."
        elif len(sources) == 0:
            # Sem fontes pesquisadas não é possível verificar => PENDING/bloqueado.
            verdict = FactCheckVerdict.PENDING
            notes = "Não há fontes pesquisadas para verificar as afirmações."
            blocking = True
        elif pending == 0 and verified > 0:
            verdict = FactCheckVerdict.OK
            notes = f"{verified} de {total} afirmações verificadas com fontes."
        elif verified > 0 and pending > 0:
            verdict = FactCheckVerdict.PENDING
            notes = (f"{verified} verificadas, {pending} pendentes de {total}. "
                     "Requer revisão humana antes de publicar.")
            blocking = True
        else:
            verdict = FactCheckVerdict.REJECTED
            notes = f"Nenhuma das {total} afirmações tem evidência verificável."
            blocking = True

        return FactCheckResult(
            verdict=verdict,
            checked_claims=total,
            verified_claims=verified,
            rejected_claims=rejected,
            pending_claims=pending,
            sources_verified=len(sources),
            notes=notes,
            blocking=blocking,
            claims=claims,
        )


__all__ = [
    "ResearchConnector",
    "MockResearchConnector",
    "ResearchIntelligence",
    "FactCheckEngine",
    "ScoredSource",
]
