"""Geração de ideias e roteiros (modo assistido).

Declara o protocolo ``LLMProvider`` e constrói o prompt que solicita ao modelo
(UMA ideia original + roteiro JSON completo em PT-BR) com todas as restrições
de fontes, duração, tipografia móvel e confiança.

O código NVIDIA NIM está fora do escopo deste módulo.
"""

from __future__ import annotations

from typing import Mapping, Protocol, Sequence, runtime_checkable

from src.scripts import KINDS, Source, TEMPLATES

__all__ = ["LLMProvider", "build_prompt"]


@runtime_checkable
class LLMProvider(Protocol):
    """Provedor de modelo de linguagem para geração de roteiros.

    ``complete`` recebe um prompt e retorna um dict (JSON do roteiro) já
    validável por ``src.scripts.load_script``.
    """

    def complete(self, prompt: str) -> dict:  # pragma: no cover - interface
        ...


def build_prompt(
    niche: str,
    kind: str,
    target_seconds: int,
    min_seconds: int,
    max_seconds: int,
    catalog: Mapping[str, Source],
    previous_topics: Sequence[str],
) -> str:
    """Constrói o prompt PT-BR que pede ao modelo UMA ideia original e o JSON
    completo do roteiro, respeitando todas as restrições do pipeline.

    O modelo não deve afirmar a veracidade de URLs: os dados são revisados por
    um operador humano (nunca há afirmação automática de verdade).
    """
    source_list = "\n".join(
        f"- {sid}: {src.title} ({src.url}) — {src.editorial_evidence}"
        for sid, src in catalog.items()
    ) or "(catálogo vazio)"

    previous = (
        "\nEvite estes tópicos já usados: " + "; ".join(previous_topics)
        if previous_topics
        else ""
    )

    return f"""
Você é roteirista especialista em vídeos curtos verticais para o TikTok (PT-BR).

OBJETIVO: Gere UMA (apenas uma) ideia ORIGINAL de vídeo para a nicho "{niche}"
e retorne um único objeto JSON com o roteiro COMPLETO, pronto para validar em
Python. Não retorne texto fora do JSON.

TIPO DE ROTEIRO: kind = "{kind}" (factual | ficcao)
DURAÇÃO ALVO: {target_seconds}s (intervalo aceito: [{min_seconds}, {max_seconds}]s)
TEMPLATES PERMITIDOS: {", ".join(TEMPLATES)}{previous}

ESTRUTURA OBRIGATÓRIA DO JSON:
{{
  "topic": "<tema curto e original>",
  "template": "<curiosidade | lista | historia | explicacao>",
  "kind": "<factual | ficcao>",
  "title": "<título, até 90 chars>",
  "description": "<descrição, até 220 chars>",
  "hashtags": ["#tag1", "#tag2", "#tag3"],  (entre 3 e 5, com '#')
  "scenes": [
    {{"role": "gancho", "seconds": 3.0, "heading": "...", "narration": ["frase 1", "frase 2"], "source_ids": ["id_fonte"]}},
    {{"role": "corpo", "seconds": 9.0, "heading": "...", "narration": ["..."], "source_ids": ["id_fonte"]}},
    {{"role": "cta", "seconds": 3.0, "heading": "...", "narration": ["..."], "source_ids": []}}
  ]
}}

REGRAS OBRIGATÓRIAS (o validador rejeita o roteiro se descumprir):
1. UMA ideia original. Não repita tópicos anteriores.
2. "scenes" deve ter 1 a 8 cenas; cada uma com role (gancho/corpo/cta),
   seconds (>0, <=30), heading (<=60 chars), narration (lista não vazia de
   frases <=130 chars cada, adequadas ao TTS da Piper) e source_ids.
3. O gancho deve ter no máximo 3 segundos e ser curto e envolvente.
4. A soma de seconds deve ficar dentro de [{min_seconds}, {max_seconds}]s.
5. Hashtags: entre 3 e 5, reconhecíveis para o público PT-BR, iniciadas com '#'.
6. O título não entra na legenda; a legenda = descrição + hashtags (<= 2200 chars).

FONTES E CONFIANÇA (leia com atenção):
- Se kind == "factual": toda cena, exceto "cta", DEVE ter ao menos um
  source_id que exista no catálogo abaixo. Fontes são revisadas por operador;
  NÃO afirme a veracidade automática de URLs.
- Somente fontes HTTPS com evidência editorial. Use apenas estes ids:
{source_list}
- Se kind == "ficcao": source_ids deve estar vazio em todas as cenas e o roteiro
  DEVE ser visivelmente marcado como ficção na legenda.
- Nunca invente fontes nem fatos. Dados são revisados por um operador humano.

Escreva narrações curtas, naturais e faladas em PT-BR, adequadas para TTS.
Retorne APENAS o objeto JSON, nada mais.
""".strip()


def idea_prompt(*args, **kwargs):  # pragma: no cover - alias de compatibilidade
    return build_prompt(*args, **kwargs)
