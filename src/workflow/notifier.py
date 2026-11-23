"""Módulo de notificação de eventos do pipeline (CP27).

Fornece:

* ``Notifier`` — protocolo de notificação com método ``notify``.
* ``ConsoleNotifier`` — imprime notificações no stdout (útil para CLI e logs).
* ``FileNotifier`` — escreve cada notificação como linha JSON em arquivo
  (JSON Lines), permitindo auditoria posterior de eventos.

Regras de segurança (AGENTS.md):
- Mocks claramente identificados; estados simulados distinguíveis de estados
  confirmados remotamente.
- Nenhuma credencial lida ou exposta.
- Testes isolados, sem rede, sem credenciais.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Protocolo                                                                   #
# --------------------------------------------------------------------------- #
@runtime_checkable
class Notifier(Protocol):
    """Protocolo de notificação de eventos do pipeline."""

    def notify(
        self,
        video_id: str,
        status: str,
        message: str,
        **fields: Any,
    ) -> None:
        """Emite uma notificação de evento.

        Args:
            video_id: Identificador do vídeo.
            status: Estágio/status do evento (ex.: QUALITY_OK, RENDER_FAILED,
                PUBLISHING, PUBLISHED).
            message: Mensagem legível descritiva do evento.
            **fields: Campos adicionais contextuais (platform, remote_id,
                confirmed_at, etc.).
        """
        ...


# --------------------------------------------------------------------------- #
# ConsoleNotifier                                                             #
# --------------------------------------------------------------------------- #
class ConsoleNotifier:
    """Notificador que imprime eventos no stdout.

    Útil para execução via CLI e para logs visíveis em tempo real. Nunca
    falha: exceções de impressão são ignoradas para não quebrar o pipeline.
    """

    def notify(
        self,
        video_id: str,
        status: str,
        message: str,
        **fields: Any,
    ) -> None:
        partes = [f"[NOTIFICAÇÃO] video_id={video_id} status={status}: {message}"]
        if fields:
            extra = " ".join(f"{k}={v}" for k, v in fields.items())
            partes.append(f"[{extra}]")
        print(" ".join(partes))


# --------------------------------------------------------------------------- #
# FileNotifier                                                                #
# --------------------------------------------------------------------------- #
class FileNotifier:
    """Notificador que escreve cada evento como linha JSON em arquivo.

    O arquivo segue o formato JSON Lines (uma linha JSON por evento), o que
    permite leitura incremental e auditoria posterior. O diretório pai é
    criado automaticamente se não existir.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def notify(
        self,
        video_id: str,
        status: str,
        message: str,
        **fields: Any,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        registro = {
            "video_id": video_id,
            "status": status,
            "message": message,
            "timestamp": _utc_now_iso(),
        }
        registro.update(fields)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(registro, ensure_ascii=False) + "\n")


__all__ = ["Notifier", "ConsoleNotifier", "FileNotifier"]
