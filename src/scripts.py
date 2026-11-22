"""Roteiros PT-BR para o TikTok AI Autopilot.

Fornece dataclasses imutáveis (``Source``, ``Scene`` e ``Script``), leitura do
catálogo de fontes (``config/sources.yaml``) e validação/montagem de roteiros a
partir de dados brutos (JSON entregue pelo modo ``assisted``).

Nenhuma requisição de rede é feita aqui: a validação é puramente local e
rápida (gancho com no máximo 3 s), de modo a ser seguro para subprocessos e TTS.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
import re
import unicodedata

import yaml

from src.errors import DuplicateError, PipelineError

__all__ = [
    "Source",
    "Scene",
    "Script",
    "read_catalog",
    "load_script",
    "normalize_topic",
    "script_fingerprint",
    "similar_topic",
    "TEMPLATES",
    "KINDS",
    "ROLES",
    "MAX_CAPTION_CHARS",
]

# --- dicionários fechados -------------------------------------------------
TEMPLATES = ("curiosidade", "lista", "historia", "explicacao")
KINDS = ("factual", "ficcao")
ROLES = ("gancho", "corpo", "cta")

# --- limites de tipografia móvel, subprocesso e TTS -----------------------
MAX_TITLE_CHARS = 90
MAX_DESCRIPTION_CHARS = 220
MAX_HEADING_CHARS = 60
MAX_PHRASE_CHARS = 130
MAX_SCENES = 8
MAX_SCENE_SECONDS = 30
HOOK_MAX_SECONDS = 3
MAX_CAPTION_CHARS = 2200  # Buffer considera <= 2200 caracteres
MIN_HASHTAGS = 3
MAX_HASHTAGS = 5

_ID_RE = re.compile(r"^[a-z0-9_-]+$")
_HASHTAG_RE = re.compile(r"^#[A-Za-z0-9À-ÖØ-öø-ÿ]+$", re.UNICODE)
_HTTPS_PREFIX = "https://"

_SCRIPT_KEYS = frozenset(
    {"topic", "template", "kind", "title", "description", "hashtags", "scenes", "sources"}
)
_SCENE_KEYS = frozenset({"role", "seconds", "heading", "narration", "source_ids"})


# --- dataclasses imutáveis ------------------------------------------------


@dataclass(frozen=True)
class Source:
    """Fonte de referência curada e revisada por um operador humano.

    Nunca é aceita do lado de entrada de ``load_script``: sempre é re-resolvida
    estritamente a partir do catálogo (allowlist) passado por parâmetro.
    """

    id: str
    title: str
    url: str
    publisher: str
    accessed_at: str
    editorial_evidence: str


@dataclass(frozen=True)
class Scene:
    """Uma cena do roteiro vertical (mobile)."""

    role: str  # gancho | corpo | cta
    seconds: float
    heading: str
    narration: tuple[str, ...]  # frases individualmente legendáveis (TTS)
    source_ids: tuple[str, ...]  # ids de fontes do catálogo (vazio para cta / ficção)


@dataclass(frozen=True)
class Script:
    """Roteiro completo, validado e imutável."""

    topic: str
    template: str  # curiosidade | lista | historia | explicacao
    kind: str  # factual | ficcao
    title: str
    description: str
    hashtags: tuple[str, ...]  # com '#', 3-5
    scenes: tuple[Scene, ...]
    sources: tuple[Source, ...]  # registros confiáveis resolvidos do catálogo

    @property
    def total_seconds(self) -> float:
        """Duração estimada falada (soma das cenas)."""
        return sum(float(scene.seconds) for scene in self.scenes)

    @property
    def needs_human_review(self) -> bool:
        """Roteiros factuais exigem revisão humana mesmo em modo AUTO."""
        return self.kind == "factual"

    @property
    def caption(self) -> str:
        """Legenda para o Buffer: descrição + hashtags, limitada a <= 2200 chars.

        O título fica nos metadados (não entra na legenda). Roteiros de ficção
        são visivelmente marcados na legenda.
        """
        parts = [p for p in (self.description, " ".join(self.hashtags)) if p]
        text = "\n".join(parts)
        if self.kind == "ficcao":
            text = "[Ficção] " + text
        if len(text) > MAX_CAPTION_CHARS:
            text = text[: MAX_CAPTION_CHARS - 1].rstrip() + "…"
        return text

    def as_dict(self) -> dict[str, Any]:
        """Dicionário pronto para JSON, incluindo os registros de fonte reais.

        Usado para snapshot de round-trip interno: ao ser devolvido a
        ``load_script``, a chave ``sources`` é aceita mas *não confiada* — as
        fontes são sempre re-resolvidas do catálogo (allowlist).
        """
        return {
            "topic": self.topic,
            "template": self.template,
            "kind": self.kind,
            "title": self.title,
            "description": self.description,
            "hashtags": list(self.hashtags),
            "scenes": [
                {
                    "role": scene.role,
                    "seconds": scene.seconds,
                    "heading": scene.heading,
                    "narration": list(scene.narration),
                    "source_ids": list(scene.source_ids),
                }
                for scene in self.scenes
            ],
            "sources": [asdict(source) for source in self.sources],
            "total_seconds": self.total_seconds,
        }


# --- leitura do catálogo --------------------------------------------------


def _parse_source(source_id: str, entry: Any) -> Source:
    if not isinstance(entry, Mapping):
        raise PipelineError(f"Fonte {source_id!r} deve ser um mapeamento")
    required = ("title", "url", "publisher", "accessed_at", "editorial_evidence")
    missing = [k for k in required if not entry.get(k)]
    if missing:
        raise PipelineError(
            f"Fonte {source_id!r} está sem campos obrigatórios: {missing}"
        )
    url = str(entry["url"])
    if not url.startswith(_HTTPS_PREFIX):
        raise PipelineError(
            f"Fonte {source_id!r} deve usar HTTPS (encontrado {url!r})"
        )
    if not str(entry["editorial_evidence"]).strip():
        raise PipelineError(
            f"Fonte {source_id!r} requer editorial_evidence (justificativa de confiança)"
        )
    return Source(
        id=source_id,
        title=str(entry["title"]).strip(),
        url=url,
        publisher=str(entry["publisher"]).strip(),
        accessed_at=str(entry["accessed_at"]).strip(),
        editorial_evidence=str(entry["editorial_evidence"]).strip(),
    )


def read_catalog(path: Path) -> dict[str, Source]:
    """Lê o catálogo curado de fontes (YAML) e retorna {id: Source}.

    Valida: id válido, HTTPS obrigatório, evidência editorial não vazia.
    Erros lançam PipelineError (nunca confia em dados externos).
    """
    path = Path(path)
    if not path.exists():
        raise PipelineError(f"Catálogo de fontes não encontrado: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - YAML mal formado
        raise PipelineError(f"Falha ao ler catálogo {path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise PipelineError("O catálogo deve ser um mapeamento de fontes por id")
    catalog: dict[str, Source] = {}
    for key, entry in data.items():
        if not isinstance(key, str) or not _ID_RE.match(key):
            raise PipelineError(f"Id de fonte inválido: {key!r}")
        if key in catalog:
            raise DuplicateError(f"Fonte duplicada no catálogo: {key!r}")
        catalog[key] = _parse_source(key, entry)
    return catalog


# --- validações auxiliares ------------------------------------------------


def _require_str(value: Any, field_name: str, max_chars: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PipelineError(f"{field_name} deve ser uma string não vazia")
    text = value.strip()
    if len(text) > max_chars:
        raise PipelineError(
            f"{field_name} excede {max_chars} caracteres (tem {len(text)})"
        )
    return text


def _validate_hashtags(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise PipelineError("hashtags deve ser uma lista")
    tags = tuple(str(t).strip() for t in value)
    if not (MIN_HASHTAGS <= len(tags) <= MAX_HASHTAGS):
        raise PipelineError(
            f"hashtags deve ter entre {MIN_HASHTAGS} e {MAX_HASHTAGS} itens (tem {len(tags)})"
        )
    for tag in tags:
        if not _HASHTAG_RE.match(tag):
            raise PipelineError(f"Hashtag inválida (use '#' + letras/números): {tag!r}")
    return tags


def _validate_scene(value: Any, catalog: Mapping[str, Source], kind: str) -> Scene:
    if not isinstance(value, Mapping):
        raise PipelineError("Cada cena deve ser um mapeamento")
    extra = set(value) - _SCENE_KEYS
    if extra:
        raise PipelineError(f"Chaves não permitidas na cena: {sorted(extra)}")

    role = str(value.get("role", "")).strip()
    if role not in ROLES:
        raise PipelineError(f"role inválido '{role}' (use um de {ROLES})")

    seconds = value.get("seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise PipelineError("seconds deve ser um número")
    seconds = float(seconds)
    if seconds <= 0:
        raise PipelineError("seconds deve ser > 0")
    if seconds > MAX_SCENE_SECONDS:
        raise PipelineError(f"seconds excede {MAX_SCENE_SECONDS}s (tem {seconds})")
    if role == "gancho" and seconds > HOOK_MAX_SECONDS:
        raise PipelineError(f"gancho deve ter no máximo {HOOK_MAX_SECONDS}s (tem {seconds})")

    heading = _require_str(value.get("heading"), "heading", MAX_HEADING_CHARS)

    narration_raw = value.get("narration")
    if not isinstance(narration_raw, (list, tuple)) or not narration_raw:
        raise PipelineError("narration deve ser uma lista não vazia de frases")
    narration = tuple(_require_str(p, "narration phrase", MAX_PHRASE_CHARS) for p in narration_raw)

    source_ids_raw = value.get("source_ids") or []
    if not isinstance(source_ids_raw, (list, tuple)):
        raise PipelineError("source_ids deve ser uma lista")
    source_ids = tuple(str(s).strip() for s in source_ids_raw if str(s).strip())
    for sid in source_ids:
        if sid not in catalog:
            raise PipelineError(f"source_id não encontrado no catálogo: {sid!r}")

    # Regras de fontes por tipo de roteiro.
    if kind == "ficcao":
        if source_ids:
            raise PipelineError("Roteiro de ficção não deve referenciar fontes (source_ids vazio)")
    else:  # factual
        if role != "cta" and not source_ids:
            raise PipelineError(
                f"Cena factual '{role}' requer ao menos uma referência de fonte"
            )

    return Scene(
        role=role,
        seconds=seconds,
        heading=heading,
        narration=narration,
        source_ids=source_ids,
    )


def _resolve_sources(scenes: tuple[Scene, ...], catalog: Mapping[str, Source], kind: str) -> tuple[Source, ...]:
    if kind == "ficcao":
        return ()
    seen: set[str] = set()
    ordered: list[Source] = []
    for scene in scenes:
        for sid in scene.source_ids:
            if sid not in seen:
                seen.add(sid)
                ordered.append(catalog[sid])
    return tuple(ordered)


# --- carregamento e validação do roteiro ---------------------------------


def load_script(
    raw: object,
    catalog: Mapping[str, Source],
    min_seconds: float,
    max_seconds: float,
) -> Script:
    """Valida e monta um :class:`Script` a partir de dados brutos (JSON).

    Nunca confia nos registros de fonte fornecidos: as fontes são re-resolvidas
    estritamente a partir do ``catalog`` (allowlist) passado por parâmetro.
    Chaves extras são rejeitadas, exceto a chave interna ``sources`` do snapshot
    de ``as_dict`` (aceita, mas ignorada para fins de confiança).

    Lança :class:`PipelineError` em qualquer erro de validação.
    """
    if not isinstance(raw, Mapping):
        raise PipelineError("Roteiro bruto deve ser um objeto JSON (mapeamento)")
    extra = set(raw) - _SCRIPT_KEYS
    if extra:
        raise PipelineError(f"Chaves não permitidas no roteiro: {sorted(extra)}")

    topic = normalize_topic(str(raw.get("topic", "")).strip())
    if not topic:
        raise PipelineError("topic é obrigatório")

    template = str(raw.get("template", "")).strip()
    if template not in TEMPLATES:
        raise PipelineError(f"template inválido '{template}' (use um de {TEMPLATES})")

    kind = str(raw.get("kind", "")).strip()
    if kind not in KINDS:
        raise PipelineError(f"kind inválido '{kind}' (use um de {KINDS})")

    title = _require_str(raw.get("title"), "title", MAX_TITLE_CHARS)
    description = _require_str(raw.get("description"), "description", MAX_DESCRIPTION_CHARS)
    hashtags = _validate_hashtags(raw.get("hashtags"))

    scenes_raw = raw.get("scenes")
    if not isinstance(scenes_raw, (list, tuple)) or not scenes_raw:
        raise PipelineError("scenes deve ser uma lista não vazia")
    if len(scenes_raw) > MAX_SCENES:
        raise PipelineError(f"scenes excede {MAX_SCENES} itens (tem {len(scenes_raw)})")
    scenes = tuple(_validate_scene(s, catalog, kind) for s in scenes_raw)

    total = sum(float(scene.seconds) for scene in scenes)
    if total < float(min_seconds) or total > float(max_seconds):
        raise PipelineError(
            f"Duração total {total:.2f}s fora do intervalo [{min_seconds}, {max_seconds}]s"
        )

    sources = _resolve_sources(scenes, catalog, kind)

    return Script(
        topic=topic,
        template=template,
        kind=kind,
        title=title,
        description=description,
        hashtags=hashtags,
        scenes=scenes,
        sources=sources,
    )


# --- helpers de tópico e impressão digital --------------------------------


def normalize_topic(text: str) -> str:
    """Normaliza um tópico para comparação (minúsculas, sem acento, espaços, sem pontuação)."""
    t = str(text).strip().lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def script_fingerprint(script: Script) -> str:
    """Impressão digital do conteúdo falado normalizado (SHA-256 hex)."""
    spoken = " ".join(
        phrase for scene in script.scenes for phrase in scene.narration
    )
    return sha256(normalize_topic(spoken).encode("utf-8")).hexdigest()


def similar_topic(left: str, right: str) -> bool:
    """Retorna True apenas quando os tópicos são conservadoramente iguais.

    Conservador: evita falso-positivo — considera similar somente se as formas
    normalizadas coincidem ou há grande sobreposição de tokens com inclusão.
    """
    a = normalize_topic(left)
    b = normalize_topic(right)
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / len(ta | tb)
    return overlap >= 0.8 and (a in b or b in a)
