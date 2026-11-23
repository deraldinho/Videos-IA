"""Testes TDD para CP27 — Notificação de eventos do pipeline.

Critérios de aceitação:
- Notifier: protocolo com método notify(video_id, status, message, **fields).
- ConsoleNotifier: imprime notificação no console (stdout) sem falhar.
- FileNotifier: escreve cada notificação como linha JSON em arquivo.
- Estados simulados distinguíveis de estados confirmados remotamente (sem PUBLISHED sem confirmação real).
- Testes isolados, sem rede, sem credenciais.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.workflow.notifier import (
    Notifier,
    ConsoleNotifier,
    FileNotifier,
)


# --------------------------------------------------------------------------- #
# Protocolo                                                                   #
# --------------------------------------------------------------------------- #
def test_notifier_is_protocol():
    """Notifier é um runtime_checkable Protocol."""
    assert hasattr(Notifier, "__subclasshook__")


def test_notifier_protocol_interface():
    """Notifier define o método notify com a assinatura esperada."""
    # ConsoleNotifier deve satisfazer o protocolo
    n = ConsoleNotifier()
    assert isinstance(n, Notifier)


# --------------------------------------------------------------------------- #
# ConsoleNotifier                                                             #
# --------------------------------------------------------------------------- #
def test_console_notifier_emite_mensagem(capsys):
    """ConsoleNotifier.imprime notificação com video_id, status e message."""
    notifier = ConsoleNotifier()
    notifier.notify(
        video_id="vid-1",
        status="QUALITY_FAILED",
        message="Qualidade não aprovada: resolução inválida",
    )
    captured = capsys.readouterr()
    assert "vid-1" in captured.out
    assert "QUALITY_FAILED" in captured.out
    assert "Qualidade não aprovada" in captured.out


def test_console_notifier_nao_falha_com_campos_extra(capsys):
    """ConsoleNotifier aceita campos adicionais sem quebrar."""
    notifier = ConsoleNotifier()
    notifier.notify(
        video_id="vid-2",
        status="PUBLISHED",
        message="Publicado com sucesso",
        platform="tiktok",
        remote_id="rem_123",
        confirmed_at="2026-01-01T00:00:00+00:00",
    )
    captured = capsys.readouterr()
    assert "vid-2" in captured.out
    assert "tiktok" in captured.out


# --------------------------------------------------------------------------- #
# FileNotifier                                                                #
# --------------------------------------------------------------------------- #
def test_file_notifier_escreve_linha_json(tmp_path):
    """FileNotifier escreve cada notificação como linha JSON no arquivo."""
    log_path = tmp_path / "notificacoes.jsonl"
    notifier = FileNotifier(log_path)
    notifier.notify(
        video_id="vid-3",
        status="RENDER_FAILED",
        message="Falha na renderização: ffmpeg indisponível",
    )
    assert log_path.exists()
    linha = log_path.read_text(encoding="utf-8").strip()
    dado = json.loads(linha)
    assert dado["video_id"] == "vid-3"
    assert dado["status"] == "RENDER_FAILED"
    assert dado["message"] == "Falha na renderização: ffmpeg indisponível"
    assert "timestamp" in dado


def test_file_notifier_multiplas_notificacoes(tmp_path):
    """FileNotifier acumula múltiplas notificações (uma por linha)."""
    log_path = tmp_path / "notificacoes.jsonl"
    notifier = FileNotifier(log_path)
    notifier.notify(video_id="vid-4", status="QUALITY_OK", message="Vídeo válido")
    notifier.notify(video_id="vid-4", status="PUBLISHING", message="Enviando para Cloudinary")
    linhas = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(linhas) == 2
    d1 = json.loads(linhas[0])
    d2 = json.loads(linhas[1])
    assert d1["status"] == "QUALITY_OK"
    assert d2["status"] == "PUBLISHING"


def test_file_notifier_cria_diretorio_pai(tmp_path):
    """FileNotifier cria o diretório pai se não existir."""
    log_path = tmp_path / "sub" / "diretorio" / "notificacoes.jsonl"
    notifier = FileNotifier(log_path)
    notifier.notify(video_id="vid-5", status="OK", message="teste")
    assert log_path.exists()
    dado = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert dado["video_id"] == "vid-5"


__all__ = []
