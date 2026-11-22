"""Testes de configuração (settings.py)."""

from __future__ import annotations

from datetime import time as _time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.settings import (
    ConfigurationError,
    load_settings,
    require_buffer_credentials,
    require_cloudinary_credentials,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_load_settings_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # config.yaml mínimo
    cfg = tmp_path / "config.yaml"
    cfg.write_text("mode: REVIEW\nniche: testes\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")

    settings = load_settings(cfg, env)
    assert settings.mode == "REVIEW"
    assert settings.live_enabled is False
    assert settings.auto_enabled is False
    assert settings.niche == "testes"
    assert settings.video.width == 1080
    assert settings.video.height == 1920
    assert settings.video.fps == 30
    assert settings.publication.timezone == "America/Sao_Paulo"
    assert settings.publication.tz == ZoneInfo("America/Sao_Paulo")
    assert settings.publication.publish_times == (
        _time(12, 0),
        _time(19, 0),
    )


def test_mode_validation(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("mode: INVALID\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Modo inválido"):
        load_settings(cfg, env)


def test_auto_requires_auto_enabled(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("mode: AUTO\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="auto_enabled"):
        load_settings(cfg, env)


def test_duration_validation(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("video:\n  min_seconds: 70\n  max_seconds: 60\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="min_seconds"):
        load_settings(cfg, env)


def test_missing_config_file(tmp_path: Path) -> None:
    cfg = tmp_path / "nao_existe.yaml"
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="não encontrado"):
        load_settings(cfg, env)


def test_require_cloudinary_credentials_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.settings import Settings, CloudinarySettings, LLMSettings, VideoSettings, PublicationSettings

    s = Settings(
        cloudinary=CloudinarySettings(),
        llm=LLMSettings(),
        video=VideoSettings(),
        publication=PublicationSettings(),
    )
    monkeypatch.delenv("CLOUDINARY_CLOUD_NAME", raising=False)
    monkeypatch.delenv("CLOUDINARY_API_KEY", raising=False)
    monkeypatch.delenv("CLOUDINARY_API_SECRET", raising=False)
    with pytest.raises(ConfigurationError, match="Credenciais do Cloudinary ausentes"):
        require_cloudinary_credentials(s)


def test_require_buffer_credentials_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUFFER_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="BUFFER_API_KEY"):
        require_buffer_credentials(__import__("src.settings", fromlist=["Settings"]).Settings())


__all__ = []
