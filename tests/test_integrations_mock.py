"""Testes de integração com mocks para CloudinaryClient e BufferClient.

Nenhuma chamada de rede real é feita: os clientes rodam em modo mock (live=False).
"""

from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from src.errors import PipelineError
from src.integrations.buffer_client import BufferClient, PENDING_STATUSES
from src.integrations.cloudinary_client import CloudinaryClient, UploadResult


# --------------------------------------------------------------------------- #
# Cloudinary (mock)                                                           #
# --------------------------------------------------------------------------- #
def test_cloudinary_mock_upload_returns_deterministic_url():
    client = CloudinaryClient(cloud_name="demo", live=False)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fh:
        fh.write(b"\x00\x00\x00\x00" * 1000)
        tmp = Path(fh.name)
    try:
        r1 = client.upload("video-1", tmp)
        r2 = client.upload("video-1", tmp)  # dedup: mesmo public_id
        assert isinstance(r1, UploadResult)
        assert r1.mock is True
        assert r1.public_id == r2.public_id
        assert r1.secure_url == r2.secure_url
        assert "demo" in r1.secure_url
        assert r1.bytes_uploaded == tmp.stat().st_size
    finally:
        tmp.unlink(missing_ok=True)


def test_cloudinary_mock_rejects_oversized_video():
    client = CloudinaryClient(cloud_name="demo", live=False, max_video_mb=1)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fh:
        fh.write(b"\x00" * (2 * 1024 * 1024))  # 2MB > 1MB
        tmp = Path(fh.name)
    try:
        with pytest.raises(PipelineError, match="excede"):
            client.upload("video-big", tmp)
    finally:
        tmp.unlink(missing_ok=True)


def test_cloudinary_mock_check_quota():
    client = CloudinaryClient(cloud_name="demo", live=False)
    usage = client.check_quota()
    assert usage["mock"] is True
    assert client.is_at_limit() is False


# --------------------------------------------------------------------------- #
# Buffer (mock)                                                               #
# --------------------------------------------------------------------------- #
def test_buffer_mock_create_post_is_idempotent():
    client = BufferClient(channel_id="ch-tiktok", live=False)
    p1 = client.create_post("video-1", "Texto do post #tech #tiktok")
    p2 = client.create_post("video-1", "Texto diferente")  # mesmo video_id -> idempotente
    assert p1.post_id == p2.post_id
    assert p1.text == "Texto do post #tech #tiktok"  # mantém o original
    assert p1.mock is True
    assert p1.status == "scheduled"


def test_buffer_mock_list_pending_returns_scheduled():
    client = BufferClient(channel_id="ch-tiktok", live=False)
    client.create_post("video-1", "Post 1 #tech")
    client.create_post("video-2", "Post 2 #tiktok")
    pending = client.list_pending_posts()
    assert len(pending) == 2
    assert all(p.status in PENDING_STATUSES for p in pending)


def test_buffer_mock_requires_channel_id():
    client = BufferClient(live=False)  # sem channel_id
    with pytest.raises(PipelineError, match="canal TikTok"):
        client.create_post("video-1", "texto")


# --------------------------------------------------------------------------- #
# Buffer: contratos oficiais (validação de valores aceitos)                   #
# --------------------------------------------------------------------------- #
def test_buffer_valid_statuses_and_modes():
    assert "scheduled" in PENDING_STATUSES
    assert "sending" in PENDING_STATUSES
    from src.integrations.buffer_client import (
        VALID_SHARE_MODES, VALID_SCHEDULING_TYPES, VALID_POST_STATUSES,
    )
    assert "customScheduled" in VALID_SHARE_MODES
    assert "addToQueue" in VALID_SHARE_MODES
    assert "automatic" in VALID_SCHEDULING_TYPES
    assert "notification" in VALID_SCHEDULING_TYPES
    for s in ("draft", "error", "needs_approval", "scheduled", "sending", "sent"):
        assert s in VALID_POST_STATUSES
    assert "pending" not in VALID_POST_STATUSES


__all__ = []
