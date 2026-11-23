"""Limpeza de fila de publicação (CP28).

Fornece:

* ``limpar_fila_publicacao(targets, queue_limit, horizon_days)`` — remove
  targets antigos além do horizonte (horizon_days) e respeita o limite de
  fila (queue_limit). Targets publicados e confirmados remotamente são
  preservados.

Regras de segurança (AGENTS.md):
- Testes isolados, sem rede, sem credenciais.
- Estados simulados distinguíveis de estados confirmados remotamente.
- Publicados confirmados nunca são removidos pela limpeza.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.workflow.state import PublicationTarget


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def limpar_fila_publicacao(
    targets: list[PublicationTarget],
    queue_limit: int = 10,
    horizon_days: int = 7,
) -> list[PublicationTarget]:
    """Limpa a fila de publicação respeitando queue_limit e horizon_days.

    Regras:
    1. Targets publicados e confirmados remotamente (publication_confirmed_at
       preenchido + local_state='published' + remote_status='published') são
       sempre preservados.
    2. Targets 'failed' com idade além de horizon_days são removidos.
    3. Targets 'published' não confirmados com idade além de horizon_days são
       removidos.
    4. Se o total exceder queue_limit, mantém apenas os mais recentes (por
       updated_at), preservando publicados confirmados.

    Args:
        targets: Lista de PublicationTarget.
        queue_limit: Limite máximo de targets na fila.
        horizon_days: Dias de retenção para targets não confirmados.

    Returns:
        Lista filtrada de PublicationTarget.
    """
    agora = datetime.now(timezone.utc)
    limite_horizonte = agora - timedelta(days=horizon_days)

    preservados: list[PublicationTarget] = []
    candidatos_remocao: list[PublicationTarget] = []

    for t in targets:
        # Sempre preservar publicados confirmados remotamente
        if (
            t.local_state == "published"
            and t.remote_status == "published"
            and t.publication_confirmed_at
        ):
            preservados.append(t)
            continue

        # Verificar idade
        updated = _parse_iso(t.updated_at) or _parse_iso(t.created_at)
        if updated is not None and updated < limite_horizonte:
            # Além do horizonte: candidato a remoção (não é publicado confirmado)
            continue
        candidatos_remocao.append(t)

    # Ordenar candidatos por updated_at (mais recentes primeiro)
    candidatos_remocao.sort(
        key=lambda t: t.updated_at or t.created_at,
        reverse=True,
    )

    # Aplicar queue_limit: manter apenas os mais recentes
    espaco_disponivel = max(0, queue_limit - len(preservados))
    mantidos = candidatos_remocao[:espaco_disponivel]

    return preservados + mantidos


__all__ = ["limpar_fila_publicacao"]
