"""Testes TDD para CP28 — Limpeza de fila de publicação (queue_limit/horizon_days).

Critérios de aceitação:
- queue_limit (PublicationSettings.queue_limit) limita quantidade de targets na fila.
- horizon_days (PublicationSettings.horizon_days) remove targets antigos além do horizonte.
- Targets 'failed' antigos além de horizon_days são removidos na limpeza.
- Targets 'published' confirmados são preservados (não removidos pela limpeza).
- Testes isolados, sem rede, sem credenciais.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.workflow.publication import PublicationTarget
from src.workflow.state import empty_state, state_to_dict


def _old_target(days_ago: int, local_state: str = "failed",
                platform: str = "tiktok") -> PublicationTarget:
    old_date = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return PublicationTarget(
        video_id=f"vid-old-{days_ago}",
        platform=platform,
        local_state=local_state,
        created_at=old_date,
        updated_at=old_date,
    )


def _recent_target(local_state: str = "queued",
                   platform: str = "tiktok") -> PublicationTarget:
    return PublicationTarget(
        video_id=f"vid-recent-{local_state}",
        platform=platform,
        local_state=local_state,
    )


# --------------------------------------------------------------------------- #
# Limpeza de fila                                                             #
# --------------------------------------------------------------------------- #
def test_limpeza_fila_remove_falhas_antigas_além_do_horizonte():
    """Remove targets 'failed' com idade além de horizon_days."""
    from src.workflow.limpeza_fila import limpar_fila_publicacao

    targets = [
        _old_target(10, "failed"),   # além de 7 dias -> remove
        _old_target(3, "failed"),    # dentro de 7 dias -> mantém
        _recent_target("queued"),    # recente -> mantém
    ]

    resultado = limpar_fila_publicacao(targets, queue_limit=10, horizon_days=7)
    ids_restantes = {t.video_id for t in resultado}
    assert "vid-old-10" not in ids_restantes
    assert "vid-old-3" in ids_restantes
    assert "vid-recent-queued" in ids_restantes


def test_limpeza_fila_preserva_publicados_confirmados():
    """Targets 'published' confirmados são preservados mesmo além do horizonte."""
    from src.workflow.limpeza_fila import limpar_fila_publicacao

    old_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    targets = [
        PublicationTarget(
            video_id="vid-pub-conf", platform="tiktok",
            local_state="published", remote_id="tt_123", remote_status="published",
            confirmed_at="2026-01-01T10:00:00+00:00",
            publication_confirmed_at="2026-01-01T10:00:00+00:00",
            created_at=old_date, updated_at=old_date,
        ),
        _old_target(10, "failed"),
    ]

    resultado = limpar_fila_publicacao(targets, queue_limit=10, horizon_days=7)
    ids_restantes = {t.video_id for t in resultado}
    assert "vid-pub-conf" in ids_restantes
    assert "vid-old-10" not in ids_restantes


def test_limpeza_fila_aplica_queue_limit():
    """Limita quantidade de targets na fila (queue_limit)."""
    from src.workflow.limpeza_fila import limpar_fila_publicacao

    # 15 targets recentes, queue_limit=10 -> mantém apenas 10 (os mais recentes)
    targets = [_recent_target("queued") for _ in range(15)]
    # Marcar timestamps diferentes para ordenação
    for i, t in enumerate(targets):
        t.created_at = f"2026-01-{i+1:02d}T00:00:00+00:00"
        t.updated_at = t.created_at

    resultado = limpar_fila_publicacao(targets, queue_limit=10, horizon_days=7)
    assert len(resultado) <= 10


def test_limpeza_fila_respeita_estado_publicado_nao_confirmado():
    """Targets 'published' não confirmados são sujeitos à limpeza por idade."""
    from src.workflow.limpeza_fila import limpar_fila_publicacao

    old_date = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    targets = [
        PublicationTarget(
            video_id="vid-pub-nao-conf", platform="tiktok",
            local_state="published", remote_id=None, remote_status=None,
            confirmed_at=None, publication_confirmed_at=None,
            created_at=old_date, updated_at=old_date,
        ),
    ]

    resultado = limpar_fila_publicacao(targets, queue_limit=10, horizon_days=7)
    # Publicado não confirmado + antigo -> sujeito à limpeza
    assert len(resultado) == 0 or all(t.video_id != "vid-pub-nao-conf" for t in resultado)


__all__ = []
