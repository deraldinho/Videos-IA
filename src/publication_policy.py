"""Política centralizada de autorização de publicação (Finding CR-05).

Ponto único de verdade para decidir se uma publicação real pode ocorrer,
evitando regras duplicadas e divergentes entre ComplianceAgent,
PublishingAgent, Orchestrator e main.py.

Invariantes aplicadas:
- Modo DRY_RUN/REVIEW: nunca autoriza publicação real (apenas simulação).
- Modo AUTO: requer auto_enabled=True E live_enabled=True.
- Qualquer modo: requer aprovação humana explícita (approval == APPROVED).
- Estados locais não confirmados remotamente (ex.: mock/queued) nunca
  equivalem a publicação real autorizada.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


class PublicationDecision:
    """Resultado da decisão de autorização de publicação."""

    def __init__(self, *, authorized: bool, reason: str,
                 checks: Optional[list[str]] = None,
                 mode: str = "", live_enabled: bool = False,
                 auto_enabled: bool = False,
                 approval_status: str = "") -> None:
        self.authorized = authorized
        self.reason = reason
        self.checks = list(checks or [])
        self.mode = mode
        self.live_enabled = live_enabled
        self.auto_enabled = auto_enabled
        self.approval_status = approval_status


# Estados que indicam que o vídeo já foi aprovado para publicação.
_APPROVED_VALUE = "approved"

# Modos que nunca autorizam publicação real.
_NON_PUBLISHING_MODES = {"DRY_RUN", "REVIEW"}

# Modos que exigem credenciais de modo live para publicar.
_REQUIRES_LIVE_MODES = {"AUTO"}


def _approval_value(state: Mapping[str, Any]) -> str:
    """Normaliza o status de aprovação do estado."""
    raw = state.get("approval_status", "pending")
    if raw is None:
        return "pending"
    return str(raw)


def can_publish(settings: Optional[Any] = None,
                state: Optional[Mapping[str, Any]] = None) -> PublicationDecision:
    """Decide se a publicação real está autorizada para o estado informado.

    Argumentos:
        settings: objeto Settings (opcional). Quando fornecido, mode,
            live_enabled e auto_enabled são lidos de settings.
        state: dicionário de estado (ex.: WorkflowState como dict). Quando
            fornecido, seus campos têm prioridade sobre settings.

    Retorna:
        PublicationDecision com authorized=True somente quando todas as
        condições de segurança são satisfeitas.
    """
    # --- Resolve os campos de decisão (state tem prioridade sobre settings) ---
    if state is not None:
        mode = str(state.get("mode", "REVIEW")).upper()
        live_enabled = bool(state.get("live_enabled", False))
        auto_enabled = bool(state.get("auto_enabled", False))
        approval_status = _approval_value(state)
    else:
        if settings is None:
            return PublicationDecision(
                authorized=False,
                reason="sem estado nem settings informados",
                checks=["estado nao informado"],
            )
        mode = str(getattr(settings, "mode", "REVIEW")).upper()
        live_enabled = bool(getattr(settings, "live_enabled", False))
        auto_enabled = bool(getattr(settings, "auto_enabled", False))
        approval_status = str(getattr(settings, "approval_status", "pending"))

    checks: list[str] = []

    # Regra 1: DRY_RUN/REVIEW nunca publicam de verdade (apenas simulacao).
    if mode in _NON_PUBLISHING_MODES:
        checks.append(f"modo {mode}: apenas simulacao, sem upload real")
        return PublicationDecision(
            authorized=False,
            reason=f"publicação real bloqueada em modo {mode} (somente simulação)",
            checks=checks, mode=mode, live_enabled=live_enabled,
            auto_enabled=auto_enabled, approval_status=approval_status,
        )

    # Regra 2: AUTO requer auto_enabled=True E live_enabled=True.
    if mode == "AUTO":
        if not auto_enabled:
            checks.append("AUTO sem auto_enabled: publicação real bloqueada")
        if not live_enabled:
            checks.append("AUTO sem live_enabled: upload/Cloudinary bloqueado")
        if not auto_enabled or not live_enabled:
            return PublicationDecision(
                authorized=False,
                reason="publicação real bloqueada em modo AUTO sem auto_enabled e/ou live_enabled",
                checks=checks, mode=mode, live_enabled=live_enabled,
                auto_enabled=auto_enabled, approval_status=approval_status,
            )

    # Regra 3 (qualquer modo): requer aprovação humana explícita.
    if approval_status != "approved":
        checks.append("sem aprovacao humana explícita: publicação bloqueada")
        return PublicationDecision(
            authorized=False,
            reason="publicação bloqueada sem aprovacao humana (approval_status != approved)",
            checks=checks, mode=mode, live_enabled=live_enabled,
            auto_enabled=auto_enabled, approval_status=approval_status,
        )

    # Todas as condições satisfeitas.
    checks.append("publicação autorizada")
    return PublicationDecision(
        authorized=True,
        reason="publicação real autorizada",
        checks=checks, mode=mode, live_enabled=live_enabled,
        auto_enabled=auto_enabled, approval_status=approval_status,
    )


__all__ = ["can_publish", "PublicationDecision"]
