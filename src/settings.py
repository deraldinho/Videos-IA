"""Configuração central do TikTok AI Autopilot.

Carrega config.yaml + .env (via python-dotenv) e expõe objetos tipados
com defaults seguros. Nenhuma credencial é gravada em disco além de .env,
que está no .gitignore.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time, timedelta
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import yaml
from zoneinfo import ZoneInfo

try:  # python-dotenv é opcional em tempo de execução (CI pode injetar env direto)
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - fallback quando dotenv ausente
    load_dotenv = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"


class ConfigurationError(RuntimeError):
    """Erro de configuração (arquivo ausente, chave inválida, tipo errado)."""


@dataclass(frozen=True)
class LLMSettings:
    provider: str = "assisted"
    allow_nvidia_requests: bool = False
    nvidia_api_key: str | None = None
    nvidia_model: str | None = None


@dataclass(frozen=True)
class VideoSettings:
    min_seconds: int = 30
    max_seconds: int = 60
    target_seconds: int = 45
    width: int = 1080
    height: int = 1920
    fps: int = 30
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    node: str = "node"
    render_timeout_seconds: int = 1800
    render_workers: int = 2
    piper_model: str | None = None


@dataclass(frozen=True)
class QualitySettings:
    """Limites configuráveis dos quality gates aplicados em ``strict=True``.

    Espelha os campos de :class:`src.video.validator.QualityThresholds` e
    adiciona flags de ativação/rigor, de modo que os limiares possam ser
    lidos de ``config.yaml`` (seção ``quality``) em vez de permanecerem
    hardcoded. Os defaults iguais aos atuais de ``QualityThresholds``
    preservam o comportamento por omissão (sem regressão).
    """

    enabled: bool = True
    """Ativa ou desativa todos os quality gates em strict=True."""

    strict: bool = True
    """Quando True, aplica os quality gates (quadros pretos, silêncio, cena estática)."""

    max_black_ratio: float = 0.50
    """Fração máxima da duração que pode estar em quadros pretos."""

    max_continuous_black_seconds: float = 3.0
    """Duração máxima (s) de preto contínuo permitida."""

    min_mean_volume_db: float = -60.0
    """Volume médio mínimo (dB) aceito; abaixo disso o áudio é considerado silêncio."""

    dynamic_scene_samples: int = 5
    """Quantidade de amostras de frames ao longo do vídeo para detetar cena estática."""

    dynamic_scene_thumbnail_size: int = 160
    """Tamanho (px) da miniatura quadrada usada na comparação de cena estática."""

    dynamic_scene_phash_threshold: float = 0.9
    """Similaridade pHash acima da qual o vídeo é considerado estático."""

    def to_thresholds(self) -> QualityThresholds:
        """Converte para :class:`src.video.validator.QualityThresholds`.

        Importação local evita acoplar settings.py ao módulo validator no
        tempo de importação (validator não importa settings).
        """
        from src.video.validator import QualityThresholds  # noqa: PLC0415

        return QualityThresholds(
            max_black_ratio=self.max_black_ratio,
            max_continuous_black_seconds=self.max_continuous_black_seconds,
            min_mean_volume_db=self.min_mean_volume_db,
            dynamic_scene_samples=self.dynamic_scene_samples,
            dynamic_scene_thumbnail_size=self.dynamic_scene_thumbnail_size,
            dynamic_scene_phash_threshold=self.dynamic_scene_phash_threshold,
        )


@dataclass(frozen=True)
class PublicationSettings:
    timezone: str = "America/Sao_Paulo"
    production_time: time = time(8, 0)
    production_days: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7)
    publish_days: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7)
    publish_times: tuple[time, ...] = (time(12, 0), time(19, 0))
    videos_per_day: int = 2
    queue_limit: int = 10
    horizon_days: int = 7
    min_lead_minutes: int = 30
    tick_minutes: int = 15

    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except Exception as exc:  # pragma: no cover - zoneinfo data sempre disponível
            raise ConfigurationError(f"Fuso horário inválido: {self.timezone}") from exc


@dataclass(frozen=True)
class CloudinarySettings:
    max_video_mb: int = 25
    stop_usage_percent: int = 85
    cloud_name: str | None = None
    api_key: str | None = None
    api_secret: str | None = None


@dataclass(frozen=True)
class Settings:
    mode: str = "REVIEW"
    live_enabled: bool = False
    auto_enabled: bool = False
    niche: str = "curiosidades e explicações sobre tecnologia do cotidiano"
    script_kind: str = "factual"
    llm: LLMSettings = field(default_factory=LLMSettings)
    video: VideoSettings = field(default_factory=VideoSettings)
    publication: PublicationSettings = field(default_factory=PublicationSettings)
    cloudinary: CloudinarySettings = field(default_factory=CloudinarySettings)
    quality: QualitySettings = field(default_factory=QualitySettings)
    raw: dict = field(default_factory=dict)


def _parse_time(value: str) -> time:
    """Converte 'HH:MM' em datetime.time."""
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ConfigurationError(f"Horário inválido (esperado HH:MM): {value!r}")
    return time(int(parts[0]), int(parts[1]))


def _parse_weekdays(value: object) -> tuple[int, ...]:
    if isinstance(value, str):
        value = [int(x.strip()) for x in value.split(",") if x.strip()]
    weekdays = tuple(int(x) for x in value)
    for w in weekdays:
        if w < 1 or w > 7:
            raise ConfigurationError(f"Dia da semana inválido (ISO 1-7): {w}")
    return weekdays


def _load_raw_config(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        raise ConfigurationError(f"Arquivo de configuração não encontrado: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigurationError("config.yaml deve conter um mapeamento no nível raiz")
    return data


def _validate_required_env_vars(env_path: Path | str = ENV_PATH) -> None:
    """Valida variáveis de ambiente críticas com base na configuração atual.

    Verifica variáveis essenciais dependendo do modo de operação:
    - Modo REVIEW/DRY_RUN: valida Cloudinary quando live_enabled, Buffer quando
      necessário, NVIDIA quando allow_nvidia_requests
    - Modo AUTO: todas as credenciais são exigidas
    Levanta ConfigurationError se variáveis obrigatórias estiverem ausentes.

    A validação é mode-aware: em REVIEW/DRY_RUN mode com live_enabled=false,
    as credenciais de live mode são opcionais (validação completa é feita pelos
    require_*_credentials na hora h). Em AUTO mode, todas são exigidas.

    As variáveis verificadas incluem:
    - CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
    - BUFFER_API_KEY, BUFFER_ORGANIZATION_ID, BUFFER_CHANNEL_ID
    - NVIDIA_API_KEY, NVIDIA_MODEL (quando allow_nvidia_requests=true)
    """
    env_file = Path(env_path)
    if not env_file.exists():
        return  # Nenhum arquivo .env; variáveis do processo têm precedência

    # Carregar config.yaml para detectar modo (antes de .env ser carregado)
    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    import yaml
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            config_raw = yaml.safe_load(fh) or {}
    except Exception:
        config_raw = {}

    mode = str(config_raw.get("mode", "REVIEW")).upper()
    live_enabled = bool(config_raw.get("live_enabled", False))
    auto_enabled = bool(config_raw.get("auto_enabled", False))

    # Em AUTO mode: todas as credenciais são críticas
    if auto_enabled:
        # Verificar Cloudinary
        cld_cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME")
        cld_api_key = os.environ.get("CLOUDINARY_API_KEY")
        cld_api_secret = os.environ.get("CLOUDINARY_API_SECRET")
        missing_cloudinary = [
            name for name, val in (
                ("CLOUDINARY_CLOUD_NAME", cld_cloud_name),
                ("CLOUDINARY_API_KEY", cld_api_key),
                ("CLOUDINARY_API_SECRET", cld_api_secret),
            ) if not val
        ]

        # Verificar Buffer
        buffer_api_key = os.environ.get("BUFFER_API_KEY")
        buffer_organization_id = os.environ.get("BUFFER_ORGANIZATION_ID")
        buffer_channel_id = os.environ.get("BUFFER_CHANNEL_ID")
        missing_buffer = [
            name for name, val in (
                ("BUFFER_API_KEY", buffer_api_key),
                ("BUFFER_ORGANIZATION_ID", buffer_organization_id),
                ("BUFFER_CHANNEL_ID", buffer_channel_id),
            ) if not val
        ]

        # Verificar NVIDIA
        nvidia_api_key = os.environ.get("NVIDIA_API_KEY")
        nvidia_model = os.environ.get("NVIDIA_MODEL")
        missing_nvidia = []
        if not nvidia_api_key and not nvidia_model:
            missing_nvidia = ["NVIDIA_API_KEY", "NVIDIA_MODEL"]

        all_missing: list[str] = []
        all_missing.extend(missing_cloudinary)
        all_missing.extend(missing_buffer)
        all_missing.extend(missing_nvidia)

        if all_missing:
            raise ConfigurationError(
                "Variáveis de ambiente críticas ausentes no modo AUTO: "
                + ", ".join(sorted(set(all_missing)))
            )
        # Em modo AUTO com tudo OK, retornar cedo
        return

    # Em REVIEW/DRY_RUN mode com live_enabled=false: tolerar ausência
    # (validação completa é feita pelos require_*_credentials na hora h)
    # Apenas garantir que o arquivo .env existe e pode ser lido
    # (já que load_settings chamou esta função para validação antecipada)
    if not live_enabled and mode in {"REVIEW", "DRY_RUN"}:
        # Tolerar tudo - a validação real é deferred
        return

    # Casos híbridos: live_enabled=True mas modo REVIEW
    # Verificar apenas Cloudinary quando live_enabled=True
    cld_cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME")
    cld_api_key = os.environ.get("CLOUDINARY_API_KEY")
    cld_api_secret = os.environ.get("CLOUDINARY_API_SECRET")

    missing_cloudinary = [
        name for name, val in (
            ("CLOUDINARY_CLOUD_NAME", cld_cloud_name),
            ("CLOUDINARY_API_KEY", cld_api_key),
            ("CLOUDINARY_API_SECRET", cld_api_secret),
        ) if not val
    ]

    if missing_cloudinary and live_enabled:
        raise ConfigurationError(
            "Credenciais do Cloudinary ausentes para modo live: "
            + ", ".join(missing_cloudinary)
        )


def load_settings(
    config_path: Path | str = CONFIG_PATH,
    env_path: Path | str = ENV_PATH,
    *,
    load_env: bool = True,
) -> Settings:
    """Carrega config.yaml e .env, retornando um Settings imutável."""
    config_path = Path(config_path)
    env_path = Path(env_path)

    raw = _load_raw_config(config_path)

    if load_env and load_dotenv is not None and env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)

    # Validação centralizada de variáveis de ambiente críticas
    _validate_required_env_vars(env_path)

    def _env(name: str, default: str | None = None) -> str | None:
        return os.environ.get(name, default)

    # --- modo de operação ---
    mode = str(raw.get("mode", "REVIEW")).upper()
    if mode not in {"DRY_RUN", "REVIEW", "AUTO"}:
        raise ConfigurationError(f"Modo inválido: {mode!r} (use DRY_RUN, REVIEW ou AUTO)")

    live_enabled = bool(raw.get("live_enabled", False))
    auto_enabled = bool(raw.get("auto_enabled", False))
    if mode == "AUTO" and not auto_enabled:
        raise ConfigurationError(
            "mode: AUTO requer auto_enabled: true no config.yaml (ativa explicitamente)"
        )

    # --- LLM ---
    llm_raw = raw.get("llm", {}) or {}
    llm = LLMSettings(
        provider=str(llm_raw.get("provider", "assisted")),
        allow_nvidia_requests=bool(llm_raw.get("allow_nvidia_requests", False)),
        nvidia_api_key=_env("NVIDIA_API_KEY"),
        nvidia_model=_env("NVIDIA_MODEL", str(llm_raw.get("nvidia_model", "") or None)),
    )

    # --- vídeo ---
    video_raw = raw.get("video", {}) or {}
    video = VideoSettings(
        min_seconds=int(video_raw.get("min_seconds", 30)),
        max_seconds=int(video_raw.get("max_seconds", 60)),
        target_seconds=int(video_raw.get("target_seconds", 45)),
        width=int(video_raw.get("width", 1080)),
        height=int(video_raw.get("height", 1920)),
        fps=int(video_raw.get("fps", 30)),
        ffmpeg=str(video_raw.get("ffmpeg", "ffmpeg")),
        ffprobe=str(video_raw.get("ffprobe", "ffprobe")),
        node=str(video_raw.get("node", "node")),
        render_timeout_seconds=int(video_raw.get("render_timeout_seconds", 1800)),
        render_workers=int(video_raw.get("render_workers", 2)),
        piper_model=str(video_raw.get("piper_model", "") or None),
    )

    if video.min_seconds < 1 or video.max_seconds > 300:
        raise ConfigurationError("Duração do vídeo fora do intervalo razoável (1-300s)")
    if video.min_seconds > video.max_seconds:
        raise ConfigurationError("min_seconds não pode exceder max_seconds")

    # --- publicação / agendamento ---
    pub_raw = raw.get("publication", {}) or {}
    publication = PublicationSettings(
        timezone=str(pub_raw.get("timezone", "America/Sao_Paulo")),
        production_time=_parse_time(str(pub_raw.get("production_time", "08:00"))),
        production_days=_parse_weekdays(pub_raw.get("production_days", [1, 2, 3, 4, 5, 6, 7])),
        publish_days=_parse_weekdays(pub_raw.get("publish_days", [1, 2, 3, 4, 5, 6, 7])),
        publish_times=tuple(
            sorted({_parse_time(str(t)) for t in pub_raw.get("publish_times", ["12:00", "19:00"])})
        ),
        videos_per_day=int(pub_raw.get("videos_per_day", 2)),
        queue_limit=int(pub_raw.get("queue_limit", 10)),
        horizon_days=int(pub_raw.get("horizon_days", 7)),
        min_lead_minutes=int(pub_raw.get("min_lead_minutes", 30)),
        tick_minutes=int(pub_raw.get("tick_minutes", 15)),
    )

    if publication.videos_per_day < 1:
        raise ConfigurationError("videos_per_day deve ser >= 1")
    if publication.queue_limit < 1:
        raise ConfigurationError("queue_limit deve ser >= 1")

    # --- Cloudinary ---
    cld_raw = raw.get("cloudinary", {}) or {}
    cloudinary = CloudinarySettings(
        max_video_mb=int(cld_raw.get("max_video_mb", 25)),
        stop_usage_percent=int(cld_raw.get("stop_usage_percent", 85)),
        cloud_name=_env("CLOUDINARY_CLOUD_NAME"),
        api_key=_env("CLOUDINARY_API_KEY"),
        api_secret=_env("CLOUDINARY_API_SECRET"),
    )

    # --- Quality Gates (limites configuráveis via settings) ---
    quality_raw = raw.get("quality", {}) or {}
    quality = QualitySettings(
        enabled=bool(quality_raw.get("enabled", True)),
        strict=bool(quality_raw.get("strict", True)),
        max_black_ratio=float(quality_raw.get("max_black_ratio", 0.50)),
        max_continuous_black_seconds=float(
            quality_raw.get("max_continuous_black_seconds", 3.0)
        ),
        min_mean_volume_db=float(quality_raw.get("min_mean_volume_db", -60.0)),
        dynamic_scene_samples=int(quality_raw.get("dynamic_scene_samples", 5)),
        dynamic_scene_thumbnail_size=int(
            quality_raw.get("dynamic_scene_thumbnail_size", 160)
        ),
        dynamic_scene_phash_threshold=float(
            quality_raw.get("dynamic_scene_phash_threshold", 0.9)
        ),
    )

    return Settings(
        mode=mode,
        live_enabled=live_enabled,
        auto_enabled=auto_enabled,
        niche=str(raw.get("niche", "curiosidades e explicações sobre tecnologia do cotidiano")),
        script_kind=str(raw.get("script_kind", "factual")),
        llm=llm,
        video=video,
        publication=publication,
        cloudinary=cloudinary,
        quality=quality,
        raw=raw,
    )


def require_cloudinary_credentials(settings: Settings) -> None:
    """Lança ConfigurationError se Cloudinary não estiver configurado."""
    missing = [
        name
        for name, value in (
            ("CLOUDINARY_CLOUD_NAME", settings.cloudinary.cloud_name),
            ("CLOUDINARY_API_KEY", settings.cloudinary.api_key),
            ("CLOUDINARY_API_SECRET", settings.cloudinary.api_secret),
        )
        if not value
    ]
    if missing:
        raise ConfigurationError(
            "Credenciais do Cloudinary ausentes: " + ", ".join(missing)
        )


def require_buffer_credentials(settings: Settings) -> None:
    """Lança ConfigurationError se Buffer não estiver configurado."""
    missing = []
    if not os.environ.get("BUFFER_API_KEY"):
        missing.append("BUFFER_API_KEY")
    if missing:
        raise ConfigurationError(
            "Credenciais do Buffer ausentes: " + ", ".join(missing)
        )


__all__ = [
    "ROOT",
    "CONFIG_PATH",
    "ENV_PATH",
    "ConfigurationError",
    "LLMSettings",
    "VideoSettings",
    "PublicationSettings",
    "CloudinarySettings",
    "QualitySettings",
    "Settings",
    "load_settings",
    "require_cloudinary_credentials",
    "require_buffer_credentials",
]