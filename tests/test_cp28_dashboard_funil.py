"""Testes TDD para CP28 — Dashboard de funil de publicação.

Critérios de aceitação:
- Dashboard HTML inclui seção de funil de publicação (uploaded → queued → published → failed).
- Funil conta targets por local_state (none, uploaded, queued, publishing, published, failed).
- Funil distingue publicados confirmados (remote_status='published' + confirmed_at).
- HTML serializável e contêm rótulos do funil.
- Testes isolados, sem rede, sem credenciais.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.workflow.publication import PublicationTarget
from src.workflow.state import empty_state, state_to_dict


def _target(local_state: str, remote_status: str | None = None,
            confirmed_at: str | None = None, publication_confirmed_at: str | None = None,
            platform: str = "tiktok", remote_id: str | None = None) -> PublicationTarget:
    return PublicationTarget(
        video_id=f"vid-{local_state}",
        platform=platform,
        local_state=local_state,
        remote_id=remote_id,
        remote_status=remote_status,
        confirmed_at=confirmed_at,
        publication_confirmed_at=publication_confirmed_at,
    )


# --------------------------------------------------------------------------- #
# Funil de publicação — contagem por local_state                              #
# --------------------------------------------------------------------------- #
def test_funil_contagem_por_local_state():
    """Funil conta targets por cada local_state."""
    from src.workflow.dashboard_funil import gerar_funil_publicacao

    targets = [
        _target("none"),
        _target("uploaded"),
        _target("queued"),
        _target("publishing"),
        _target("published", remote_status="published",
                confirmed_at="2026-01-15T10:00:00+00:00",
                publication_confirmed_at="2026-01-15T10:00:00+00:00"),
        _target("failed"),
    ]

    funil = gerar_funil_publicacao(targets)
    assert funil["none"] == 1
    assert funil["uploaded"] == 1
    assert funil["queued"] == 1
    assert funil["publishing"] == 1
    assert funil["published"] == 1
    assert funil["failed"] == 1
    assert funil["total"] == 6


def test_funil_publicados_confirmados():
    """Funil separa publicados confirmados (remote_status=published + confirmed_at)."""
    from src.workflow.dashboard_funil import gerar_funil_publicacao

    targets = [
        _target("published", remote_status="published",
                confirmed_at="2026-01-15T10:00:00+00:00",
                publication_confirmed_at="2026-01-15T10:00:00+00:00"),
        _target("published", remote_status=None, confirmed_at=None),  # não confirmado
    ]

    funil = gerar_funil_publicacao(targets)
    assert funil["published"] == 2
    assert funil["published_confirmed"] == 1
    assert funil["published_not_confirmed"] == 1


def test_funil_lista_vazia():
    """Funil com lista vazia retorna zeros."""
    from src.workflow.dashboard_funil import gerar_funil_publicacao

    funil = gerar_funil_publicacao([])
    assert funil["total"] == 0
    assert funil["published"] == 0
    assert funil["published_confirmed"] == 0


# --------------------------------------------------------------------------- #
# Dashboard HTML com seção de funil                                           #
# --------------------------------------------------------------------------- #
def test_dashboard_html_contem_secao_funil(tmp_path):
    """Dashboard HTML inclui seção de funil de publicação."""
    from src.workflow.dashboard_funil import gerar_dashboard_com_funil

    out = tmp_path / "dashboard.html"
    targets = [
        _target("uploaded"),
        _target("queued"),
        _target("published", remote_status="published",
                confirmed_at="2026-01-15T10:00:00+00:00",
                publication_confirmed_at="2026-01-15T10:00:00+00:00"),
        _target("failed"),
    ]

    gerar_dashboard_com_funil(targets, output=out)
    html = out.read_text(encoding="utf-8")

    assert "Funil de Publicação" in html or "funil" in html.lower()
    assert "uploaded" in html.lower()
    assert "queued" in html.lower()
    assert "published" in html.lower()
    assert "failed" in html.lower()


def test_dashboard_html_nao_contem_credenciais(tmp_path):
    """Dashboard não expõe credenciais ou tokens."""
    from src.workflow.dashboard_funil import gerar_dashboard_com_funil

    out = tmp_path / "dashboard.html"
    targets = [_target("published", remote_id="tt_123")]
    gerar_dashboard_com_funil(targets, output=out)
    html = out.read_text(encoding="utf-8")

    # remote_id é um identificador, não credencial; não deve conter palavras como token/secret/key
    lower = html.lower()
    assert "api_key" not in lower
    assert "api_secret" not in lower
    assert "access_token" not in lower


__all__ = []
