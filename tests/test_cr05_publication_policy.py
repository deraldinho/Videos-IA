"""Testes de regressão CR-05: política centralizada de autorização de publicação.

Defeito: as regras que decidem se uma publicação real pode ocorrer estão
espalhadas e duplicadas entre ComplianceAgent, PublishingAgent, Orchestrator
e main.py. Não há um único ponto de verdade, o que permite publicação sem as
verificações adequadas quando um agente é chamado isoladamente (sem o fluxo
do grafo) e causa inconsistências entre modos DRY_RUN/REVIEW/AUTO.

Regra: existir uma função unica ``can_publish`` que seja o ponto unico de
decisao, aplicando:
- DRY_RUN/REVIEW: nunca publica de verdade (apenas simulacao).
- AUTO: requer auto_enabled=True E live_enabled=True.
- Qualquer modo: requer aprovacao humana (approval == APPROVED).
"""

from __future__ import annotations

import pytest

from src.publication_policy import can_publish, PublicationDecision
from src.workflow.state import ApprovalStatus


def _decision(mode="REVIEW", live_enabled=False, auto_enabled=False,
              approval=ApprovalStatus.PENDING.value) -> PublicationDecision:
    state = {
        "mode": mode,
        "live_enabled": live_enabled,
        "auto_enabled": auto_enabled,
        "approval_status": approval,
    }
    return can_publish(settings=None, state=state)


# --------------------------------------------------------------------------- #
# CR-05: ponto unico de verdade para autorizacao de publicacao                #
# --------------------------------------------------------------------------- #
def test_can_publish_review_nao_autoriza_publicacao_real():
    """Modo REVIEW nunca autoriza publicacao real."""
    dec = _decision(mode="REVIEW", live_enabled=True,
                    approval=ApprovalStatus.APPROVED.value)
    assert dec.authorized is False
    assert "REVIEW" in dec.reason or "simulacao" in dec.reason


def test_can_publish_dry_run_nao_autoriza_publicacao_real():
    """Modo DRY_RUN nunca autoriza publicacao real."""
    dec = _decision(mode="DRY_RUN", live_enabled=True,
                    approval=ApprovalStatus.APPROVED.value)
    assert dec.authorized is False


def test_can_publish_auto_sem_auto_enabled_bloqueado():
    """Modo AUTO sem auto_enabled nao autoriza."""
    dec = _decision(mode="AUTO", live_enabled=True,
                    approval=ApprovalStatus.APPROVED.value)
    assert dec.authorized is False
    assert any("auto_enabled" in c for c in dec.checks)


def test_can_publish_auto_sem_live_enabled_bloqueado():
    """Modo AUTO sem live_enabled nao autoriza."""
    dec = _decision(mode="AUTO", auto_enabled=True,
                    approval=ApprovalStatus.APPROVED.value)
    assert dec.authorized is False
    assert any("live_enabled" in c for c in dec.checks)


def test_can_publish_auto_completo_autoriza():
    """Modo AUTO com auto_enabled + live_enabled + aprovado autoriza."""
    dec = _decision(mode="AUTO", live_enabled=True, auto_enabled=True,
                    approval=ApprovalStatus.APPROVED.value)
    assert dec.authorized is True


def test_can_publish_sem_aprovacao_humana_bloqueado():
    """Sem aprovacao humana, nao autoriza mesmo em modo AUTO completo."""
    dec = _decision(mode="AUTO", live_enabled=True, auto_enabled=True,
                    approval=ApprovalStatus.PENDING.value)
    assert dec.authorized is False
    assert any("aprovacao" in c.lower() for c in dec.checks)


def test_can_publish_retorna_checks_nao_vazio_quando_bloqueado():
    """Quando bloqueado, a lista de checks nao esta vazia (auditavel)."""
    dec = _decision(mode="REVIEW")
    assert dec.authorized is False
    assert dec.checks


__all__ = []
