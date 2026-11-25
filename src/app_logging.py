"""Logging estruturado (JSON) para o TikTok AI Autopilot.

Fornece um logger centralizado que emite registros em JSON, com níveis,
rotação básica e filtragem de credenciais (nunca grava secrets nos logs).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any


class _SecretFilter(logging.Filter):
    """Remove valores que pareçam credenciais dos records de log."""

    SECRET_KEYS = {
        "api_key", "api_secret", "secret", "token", "password",
        "nvidia_api_key", "cloudinary_api_secret", "buffer_api_key",
    }

    def filter(self, record: logging.LogRecord) -> bool:
        # Sanitiza args e msg se contiverem chaves sensíveis.
        if record.args:
            record.args = tuple(
                self._sanitize(a) if isinstance(a, dict) else a
                for a in record.args
            )
        record.msg = self._sanitize_str(str(record.msg))
        return True

    def _sanitize(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: ("***" if k.lower() in self.SECRET_KEYS else v)
                for k, v in obj.items()
            }
        return obj

    def _sanitize_str(self, s: str) -> str:
        # Remove possíveis URLs com API keys embutidas (muito simplista).
        import re
        s = re.sub(r'(api[_-]?key|secret|token)["\']?\s*[:=]\s*["\']?[^\s"&\']+',
                   r'\1=***', s, flags=re.IGNORECASE)
        return s


class JsonFormatter(logging.Formatter):
    """Formatter que emite cada log como uma linha JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_logger_configured = False


def setup_logging(
    level: str | None = None,
    log_file: str | None = None,
    json_format: bool = True,
) -> None:
    """Configura o logging raiz da aplicação.

    - Nível via parâmetro, env ``LOG_LEVEL`` ou default ``INFO``.
    - Saída para stdout (e opcionalmente arquivo).
    - Formato JSON por padrão.
    - Filtro de secrets para não vazar credenciais.
    """
    global _logger_configured
    if _logger_configured:
        return

    level_name = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    level_val = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(level_val)

    # Remove handlers existentes para evitar duplicação em recarregamento.
    for h in list(root.handlers):
        root.removeHandler(h)

    handler: logging.Handler
    if log_file:
        handler = logging.FileHandler(log_file, encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stdout)

    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        ))
    handler.addFilter(_SecretFilter())
    root.addHandler(handler)

    # Silencia loggers muito verbosos de bibliotecas de terceiros.
    for name in ("urllib3", "requests", "langgraph", "langchain"):
        logging.getLogger(name).setLevel(logging.WARNING)

    _logger_configured = True


def get_logger(name: str) -> logging.Logger:
    """Devolve um logger configurado para o nome dado."""
    if not _logger_configured:
        setup_logging()
    return logging.getLogger(name)


__all__ = ["setup_logging", "get_logger", "JsonFormatter"]
