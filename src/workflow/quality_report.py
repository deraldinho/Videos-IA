"""Módulo de relatório de qualidade do vídeo (CP27).

Fornece:

* ``gerar_relatorio_qualidade(state)`` — constrói um dicionário serializável
  (JSON-amigável) com o resultado da validação de qualidade do vídeo a partir
  do estado do workflow.

O relatório distingue claramente estados simulados de estados confirmados
remotamente: ``valid`` reflete apenas o resultado do quality gate local
(FFprobeValidator), nunca uma confirmação remota de publicação.
"""

from __future__ import annotations

from typing import Any

from src.workflow.state import _utc_now_iso


def gerar_relatorio_qualidade(state: dict[str, Any]) -> dict[str, Any]:
    """Gera um relatório de qualidade a partir do estado do workflow.

    O relatório inclui:

    * ``video_id``, ``execution_id`` — identificadores da execução.
    * ``stage``, ``status`` — estágio/status atual do pipeline.
    * ``valid`` — se o quality gate passou (True) ou falhou (False).
    * ``errors`` — lista de erros de validação (vazia quando válido).
    * ``width``, ``height``, ``fps``, ``duration_seconds``, ``has_audio`` —
      metadados do vídeo, quando disponíveis.
    * ``timestamp`` — instante de geração do relatório.

    Quando não há ``validation_results`` no estado, ``valid`` é False e
    ``errors`` contém uma mensagem descritiva.

    Returns:
        Dict serializável (JSON-amigável) com o relatório de qualidade.
    """
    video_id = state.get("video_id", "")
    execution_id = state.get("execution_id", "")
    stage = state.get("current_stage", "")
    status = state.get("status", "")

    vr = state.get("validation_results")
    if not vr:
        return {
            "video_id": video_id,
            "execution_id": execution_id,
            "stage": stage,
            "status": status,
            "valid": False,
            "errors": ["Não há resultado de validação de qualidade no estado"],
            "width": None,
            "height": None,
            "fps": None,
            "duration_seconds": None,
            "has_audio": False,
            "timestamp": _utc_now_iso(),
        }

    return {
        "video_id": video_id,
        "execution_id": execution_id,
        "stage": stage,
        "status": status,
        "valid": bool(vr.get("valid", False)),
        "errors": list(vr.get("errors", []) or []),
        "width": vr.get("width"),
        "height": vr.get("height"),
        "fps": vr.get("fps"),
        "duration_seconds": vr.get("duration_seconds"),
        "has_audio": bool(vr.get("has_audio", False)),
        "timestamp": _utc_now_iso(),
    }


__all__ = ["gerar_relatorio_qualidade"]
