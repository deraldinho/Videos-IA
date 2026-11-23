"""Testes TDD para CP28 — Confirmação remota de publicação.

Critérios de aceitação:
- TikTokAdapter.get_remote_status() em modo live consulta API remota real.
  Só marca local_state='published' com remote_id + remote_status + confirmed_at.
- Em modo mock, nunca marca como publicado (sem confirmação remota real).
- PublicationTarget publication_confirmed_at preenchido quando confirmado.
- Estados simulados distinguíveis de estados confirmados remotamente.
- Testes isolados, sem rede, sem credenciais.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.workflow.publication import PublicationTarget, TikTokAdapter
from src.workflow.state import empty_state, state_to_dict


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _make_tmp_mp4(tmp_path: Path, name: str = "video.mp4") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x00\x00\x00\x00fake-mp4")
    return p


class _FakeCloudinary:
    def __init__(self, live: bool = False) -> None:
        self.live = live
        self.uploaded: list[tuple[str, str]] = []

    def upload(self, video_id: str, file_path):
        self.uploaded.append((video_id, str(file_path)))
        return SimpleNamespace(
            public_id=f"cp28_{video_id}",
            secure_url=f"https://res.cloudinary.com/fake/video/upload/v1/{video_id}.mp4",
            url=f"https://res.cloudinary.com/fake/video/upload/v1/{video_id}.mp4",
            bytes_uploaded=12345,
            duration_seconds=10.0,
            mock=not self.live,
        )


class _FakeBuffer:
    def __init__(self, live: bool = False) -> None:
        self.live = live
        self.posts: dict[str, dict] = {}

    def create_post(self, video_id, text, channel_id, due_at=None, video_url=None):
        if video_id in self.posts:
            return SimpleNamespace(
                post_id=self.posts[video_id]["post_id"],
                status=self.posts[video_id]["status"],
                channel_id=channel_id,
                text=text,
                due_at=due_at,
                mock=not self.live,
            )
        post_id = f"buf_{video_id}"
        self.posts[video_id] = {
            "post_id": post_id,
            "status": "scheduled",
            "channel_id": channel_id,
            "text": text,
            "due_at": due_at,
        }
        return SimpleNamespace(
            post_id=post_id,
            status="scheduled",
            channel_id=channel_id,
            text=text,
            due_at=due_at,
            mock=not self.live,
        )


# --------------------------------------------------------------------------- #
# TikTokAdapter — modo mock nunca publica                                     #
# --------------------------------------------------------------------------- #
def test_tiktok_adapter_mock_nao_confirma_publicado():
    """Em modo mock, get_remote_status nunca marca como published."""
    adapter = TikTokAdapter(live=False)
    target = PublicationTarget(
        video_id="vid-1", platform="tiktok", local_state="queued",
        remote_id="buf_vid-1", remote_status="scheduled",
    )
    out = adapter.get_remote_status(target)
    assert out.local_state != "published"
    assert out.confirmed_at is None


def test_tiktok_adapter_mock_mantem_estado_atual():
    """Em modo mock, estado local é mantido (não avança para published)."""
    adapter = TikTokAdapter(live=False)
    target = PublicationTarget(
        video_id="vid-2", platform="tiktok", local_state="uploaded",
    )
    out = adapter.get_remote_status(target)
    assert out.local_state == "uploaded"
    assert out.local_state != "published"


# --------------------------------------------------------------------------- #
# TikTokAdapter — modo live com confirmação remota real                       #
# --------------------------------------------------------------------------- #
class _FakeRemoteClient:
    """Cliente remoto fake que simula confirmação da API do TikTok."""

    def __init__(self, published: bool = False, remote_id: str = "tt_remote_123",
                 remote_status: str = "published"):
        self.published = published
        self.remote_id = remote_id
        self.remote_status = remote_status

    def get_video_status(self, video_id: str) -> dict:
        if self.published:
            return {
                "published": True,
                "remote_id": self.remote_id,
                "remote_status": self.remote_status,
                "confirmed_at": "2026-01-15T10:00:00+00:00",
            }
        return {
            "published": False,
            "remote_status": "pending",
        }


def test_tiktok_adapter_live_get_remote_status_confirma_publicado(monkeypatch):
    """Em modo live, get_remote_status com confirmação remota real marca published."""
    adapter = TikTokAdapter(live=True)
    # Injetar cliente remoto fake com confirmação publicada
    adapter._remote_client = _FakeRemoteClient(published=True)

    target = PublicationTarget(
        video_id="vid-3", platform="tiktok", channel_id="ch-1",
        local_state="queued", remote_id="buf_vid-3", remote_status="scheduled",
        cloudinary_url="https://res.cloudinary.com/fake/video/upload/v1/vid-3.mp4",
    )
    out = adapter.get_remote_status(target)

    assert out.local_state == "published"
    assert out.remote_id == "tt_remote_123"
    assert out.remote_status == "published"
    assert out.confirmed_at is not None
    assert out.publication_confirmed_at is not None


def test_tiktok_adapter_live_nao_publica_sem_confirmacao_remota():
    """Em modo live, sem confirmação remota, não marca published."""
    adapter = TikTokAdapter(live=True)
    adapter._remote_client = _FakeRemoteClient(published=False)

    target = PublicationTarget(
        video_id="vid-4", platform="tiktok", local_state="queued",
        remote_id="buf_vid-4", remote_status="scheduled",
    )
    out = adapter.get_remote_status(target)
    assert out.local_state != "published"
    assert out.confirmed_at is None
    assert out.publication_confirmed_at is None


def test_tiktok_adapter_live_sem_remote_client_erro():
    """Em modo live sem _remote_client, registra erro e não publica."""
    adapter = TikTokAdapter(live=True)
    target = PublicationTarget(
        video_id="vid-5", platform="tiktok", local_state="queued",
    )
    out = adapter.get_remote_status(target)
    assert out.local_state != "published"
    assert out.error_message is not None
    assert "credenciais" in out.error_message.lower()


def test_tiktok_adapter_nao_publica_sem_confirmacao_remota():
    """Não marca published sem remote_id + remote_status + confirmed_at."""
    adapter = TikTokAdapter(live=False)
    target = PublicationTarget(
        video_id="vid-6", platform="tiktok", local_state="queued",
        remote_id=None, remote_status=None, confirmed_at=None,
    )
    out = adapter.get_remote_status(target)
    assert out.local_state != "published"
    assert out.confirmed_at is None
    assert out.publication_confirmed_at is None


# --------------------------------------------------------------------------- #
# PublicationTarget — campo publication_confirmed_at                          #
# --------------------------------------------------------------------------- #
def test_publication_target_tem_publication_confirmed_at():
    """PublicationTarget tem campo publication_confirmed_at (None por padrão)."""
    t = PublicationTarget(video_id="vid-5", platform="tiktok")
    assert hasattr(t, "publication_confirmed_at")
    assert t.publication_confirmed_at is None


def test_publication_target_publication_confirmed_at_preenchido():
    """publication_confirmed_at é preenchido quando confirmado."""
    t = PublicationTarget(
        video_id="vid-6", platform="tiktok", local_state="published",
        remote_id="tt_123", remote_status="published",
        confirmed_at="2026-01-15T10:00:00+00:00",
        publication_confirmed_at="2026-01-15T10:00:00+00:00",
    )
    assert t.publication_confirmed_at == "2026-01-15T10:00:00+00:00"
    assert t.local_state == "published"


def test_publication_target_round_trip_com_publication_confirmed_at():
    """publication_confirmed_at faz round-trip por model_dump/model_validate."""
    t = PublicationTarget(
        video_id="vid-7", platform="tiktok", local_state="published",
        publication_confirmed_at="2026-01-15T10:00:00+00:00",
    )
    dumped = t.model_dump(mode="json")
    restored = PublicationTarget.model_validate(dumped)
    assert restored.publication_confirmed_at == "2026-01-15T10:00:00+00:00"


__all__ = []
