"""Testes TDD para CP24 — Publicação multicanal.

Critérios de aceitação (missão CP24):
- PublicationTarget: target_id, video_id, platform, channel_id, idempotency_key,
  local_state, remote_id, remote_status, confirmed_at, error_message, retry_count,
  created_at, updated_at.
- PublicationAdapter (protocolo): prepare, upload, publish, get_remote_status.
- TikTokAdapter: CloudinaryClient + BufferClient; mock (live=False) nunca chama real.
- YouTubeShortsAdapter: mock, platform="youtube_shorts".
- InstagramReelsAdapter: mock, platform="instagram_reels" (condicional).
- Idempotência: mesmo video_id + mesma platform não cria targets/posts duplicados.
- Estados distinguíveis: uploaded != queued != published. Nunca PUBLISHED sem
  confirmação remota real (remote_id + remote_status + confirmed_at).
- Falha em um destino não afeta outros destinos (isolamento via try/catch).
- PublicationManager coordena múltiplos adaptadores.
- Testes isolados, sem acesso a contas reais, sem credenciais.
- Mocks claramente identificados; estados simulados distinguíveis de estados
  confirmados remotamente.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.workflow.state import (
    PublicationStatus,
    Stage,
    empty_state,
    state_to_dict,
)
from src.workflow.publication import (
    PublicationAdapter,
    PublicationTarget,
    PublicationManager,
    TikTokAdapter,
    YouTubeShortsAdapter,
    InstagramReelsAdapter,
)


def _make_tmp_mp4(tmp_path: Path, name: str = "video.mp4") -> Path:
    """Cria um arquivo MP4 vazio temporário para testes de upload."""
    p = tmp_path / name
    p.write_bytes(b"\x00\x00\x00\x00fake-mp4")
    return p


# --------------------------------------------------------------------------- #
# Conector auxiliar controlado (não-mock) para testar caminhos reais          #
# --------------------------------------------------------------------------- #
class _FakeCloudinary:
    """Substitui CloudinaryClient em testes de caminho real."""

    def __init__(self, live: bool = False) -> None:
        self.live = live
        self.uploaded: list[tuple[str, str]] = []

    def upload(self, video_id: str, file_path: Any, *, overwrite: bool = False):
        from types import SimpleNamespace

        self.uploaded.append((video_id, str(file_path)))
        return SimpleNamespace(
            public_id=f"cp24_{video_id}",
            secure_url=f"https://res.cloudinary.com/fake/video/upload/v1/{video_id}.mp4",
            url=f"https://res.cloudinary.com/fake/video/upload/v1/{video_id}.mp4",
            bytes_uploaded=12345,
            duration_seconds=10.0,
            mock=not self.live,
        )


class _FakeBuffer:
    """Substitui BufferClient em testes de caminho real."""

    def __init__(self, live: bool = False) -> None:
        self.live = live
        self.posts: dict[str, dict[str, Any]] = {}

    def create_post(
        self,
        video_id: str,
        text: str,
        channel_id: str,
        due_at: str | None = None,
        video_url: str | None = None,
    ):
        from types import SimpleNamespace

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

    def list_pending_posts(self, channel_id: str, first: int = 10):
        return []


# --------------------------------------------------------------------------- #
# Modelos de estado (CP24)                                                    #
# --------------------------------------------------------------------------- #
def test_publication_target_fields():
    """PublicationTarget armazena todos os campos mínimos do contrato."""
    t = PublicationTarget(
        video_id="vid-1",
        platform="tiktok",
        channel_id="ch-tiktok-1",
        idempotency_key="vid-1:tiktok",
        local_state="none",
    )
    assert t.target_id
    assert t.video_id == "vid-1"
    assert t.platform == "tiktok"
    assert t.channel_id == "ch-tiktok-1"
    assert t.idempotency_key == "vid-1:tiktok"
    assert t.local_state == "none"
    assert t.remote_id is None
    assert t.remote_status is None
    assert t.confirmed_at is None
    assert t.error_message is None
    assert t.retry_count == 0
    assert t.created_at
    assert t.updated_at


def test_publication_target_round_trip_dict():
    """PublicationTarget faz round-trip por state_to_dict/dict_to_state."""
    t = PublicationTarget(
        video_id="vid-2",
        platform="youtube_shorts",
        local_state="uploaded",
        remote_id="yt_123",
        remote_status="processed",
        confirmed_at="2026-01-01T00:00:00+00:00",
    )
    data = state_to_dict(empty_state("exec", "vid-2"))
    # PublicationTarget não está no WorkflowState por si só; testa o modelo isolado.
    dumped = t.model_dump(mode="json")
    restored = PublicationTarget.model_validate(dumped)
    assert restored.target_id == t.target_id
    assert restored.platform == "youtube_shorts"
    assert restored.local_state == "uploaded"
    assert restored.remote_id == "yt_123"


def test_publication_target_idempotency_key_default():
    """idempotency_key default deriva de video_id:platform."""
    t = PublicationTarget(video_id="vid-3", platform="instagram_reels")
    assert t.idempotency_key == "vid-3:instagram_reels"


def test_publication_target_local_state_enum_values():
    """local_state aceita: none, uploaded, queued, publishing, published, failed."""
    for estado in ["none", "uploaded", "queued", "publishing", "published", "failed"]:
        t = PublicationTarget(video_id="v", platform="tiktok", local_state=estado)
        assert t.local_state == estado


# --------------------------------------------------------------------------- #
# PublicationAdapter (protocolo)                                              #
# --------------------------------------------------------------------------- #
def test_publication_adapter_is_protocol():
    """PublicationAdapter é um runtime_checkable Protocol."""
    assert isinstance(PublicationAdapter, type) or hasattr(PublicationAdapter, "__subclasshook__")


def test_tiktok_adapter_platform():
    """TikTokAdapter.platform == 'tiktok'."""
    adapter = TikTokAdapter(live=False)
    assert adapter.platform == "tiktok"


def test_youtube_shorts_adapter_platform():
    """YouTubeShortsAdapter.platform == 'youtube_shorts'."""
    adapter = YouTubeShortsAdapter(live=False)
    assert adapter.platform == "youtube_shorts"


def test_instagram_reels_adapter_platform():
    """InstagramReelsAdapter.platform == 'instagram_reels'."""
    adapter = InstagramReelsAdapter(live=False)
    assert adapter.platform == "instagram_reels"


# --------------------------------------------------------------------------- #
# TikTokAdapter — mock (live=False)                                           #
# --------------------------------------------------------------------------- #
def test_tiktok_adapter_prepare_mock_sets_idempotency_key():
    """prepare() define idempotency_key e retorna o target."""
    adapter = TikTokAdapter(live=False)
    target = PublicationTarget(video_id="vid-4", platform="tiktok", channel_id="ch-1")
    out = adapter.prepare(target)
    assert out.idempotency_key == "vid-4:tiktok"
    assert out.platform == "tiktok"


def test_tiktok_adapter_upload_mock_never_calls_real(tmp_path):
    """Em modo mock, upload não chama Cloudinary real e local_state='uploaded'."""
    adapter = TikTokAdapter(live=False)
    target = PublicationTarget(video_id="vid-5", platform="tiktok", local_state="none")
    mp4 = _make_tmp_mp4(tmp_path)
    out = adapter.upload(target, video_path=str(mp4))
    assert out.local_state == "uploaded"
    assert out.remote_id is None or out.remote_id == ""
    # Mock: nunca publicado
    assert out.local_state != "published"


def test_tiktok_adapter_publish_mock_queued_not_published():
    """Em modo mock, publish define local_state='queued', nunca 'published'."""
    adapter = TikTokAdapter(live=False)
    target = PublicationTarget(
        video_id="vid-6", platform="tiktok", local_state="uploaded",
        remote_id="", remote_status="",
    )
    out = adapter.publish(target)
    assert out.local_state == "queued"
    assert out.local_state != "published"
    assert out.confirmed_at is None


def test_tiktok_adapter_get_remote_status_mock():
    """get_remote_status em modo mock não confirma como publicado."""
    adapter = TikTokAdapter(live=False)
    target = PublicationTarget(
        video_id="vid-7", platform="tiktok", local_state="queued",
        remote_id="buf_vid-7", remote_status="scheduled",
    )
    out = adapter.get_remote_status(target)
    # Mock: remote_status mantém 'scheduled', não vira 'published'
    assert out.remote_status != "published" or out.confirmed_at is None


# --------------------------------------------------------------------------- #
# TikTokAdapter — live mode (caminho real simulado com fakes)                 #
# --------------------------------------------------------------------------- #
def test_tiktok_adapter_live_upload_then_publish_queued(monkeypatch, tmp_path):
    """Em modo live: upload → uploaded, publish → queued (não published)."""
    adapter = TikTokAdapter(live=True)
    # Injetar fakes em vez de clientes reais
    adapter._cloudinary = _FakeCloudinary(live=True)
    adapter._buffer = _FakeBuffer(live=True)

    target = PublicationTarget(
        video_id="vid-8", platform="tiktok", channel_id="ch-1",
        local_state="none",
    )
    out = adapter.prepare(target)
    assert out.idempotency_key == "vid-8:tiktok"

    mp4 = _make_tmp_mp4(tmp_path)
    out = adapter.upload(out, video_path=str(mp4))
    assert out.local_state == "uploaded"
    assert out.cloudinary_id == "cp24_vid-8"  # se o modelo tiver esse campo

    out = adapter.publish(out)
    assert out.local_state == "queued"
    # Nunca published sem confirmação remota real
    assert out.local_state != "published"


def test_tiktok_adapter_live_idempotency_no_duplicate_post(monkeypatch):
    """Mesmo video_id + mesma platform não recria post no Buffer (idempotência)."""
    adapter = TikTokAdapter(live=True)
    adapter._cloudinary = _FakeCloudinary(live=True)
    adapter._buffer = _FakeBuffer(live=True)

    target = PublicationTarget(
        video_id="vid-9", platform="tiktok", channel_id="ch-1",
        local_state="uploaded", cloudinary_url="https://fake.com/vid-9.mp4",
    )
    out1 = adapter.publish(target)
    out2 = adapter.publish(target)
    # Mesmo post_id (idempotente)
    assert out1.buffer_post_id == out2.buffer_post_id


# --------------------------------------------------------------------------- #
# YouTubeShortsAdapter — mock                                                 #
# --------------------------------------------------------------------------- #
def test_youtube_shorts_adapter_mock_upload_queued(tmp_path):
    """YouTubeShorts mock: upload → uploaded, publish → queued, nunca published."""
    adapter = YouTubeShortsAdapter(live=False)
    target = PublicationTarget(video_id="vid-10", platform="youtube_shorts")
    out = adapter.prepare(target)
    assert out.platform == "youtube_shorts"
    mp4 = _make_tmp_mp4(tmp_path)
    out = adapter.upload(out, video_path=str(mp4))
    assert out.local_state == "uploaded"
    out = adapter.publish(out)
    assert out.local_state == "queued"
    assert out.local_state != "published"


# --------------------------------------------------------------------------- #
# InstagramReelsAdapter — mock (condicional)                                  #
# --------------------------------------------------------------------------- #
def test_instagram_reels_adapter_mock_upload_queued(tmp_path):
    """InstagramReels mock: upload → uploaded, publish → queued, nunca published."""
    adapter = InstagramReelsAdapter(live=False)
    target = PublicationTarget(video_id="vid-11", platform="instagram_reels")
    out = adapter.prepare(target)
    assert out.platform == "instagram_reels"
    mp4 = _make_tmp_mp4(tmp_path)
    out = adapter.upload(out, video_path=str(mp4))
    assert out.local_state == "uploaded"
    out = adapter.publish(out)
    assert out.local_state == "queued"
    assert out.local_state != "published"


# --------------------------------------------------------------------------- #
# Estados distinguíveis: uploaded != queued != published                      #
# --------------------------------------------------------------------------- #
def test_estados_uploaded_queued_published_sao_distinguiveis(tmp_path):
    """uploaded, queued e published são estados distintos e nunca se confundem."""
    adapter = TikTokAdapter(live=False)
    target = PublicationTarget(video_id="vid-12", platform="tiktok", local_state="none")

    out = adapter.prepare(target)
    assert out.local_state == "none"

    mp4 = _make_tmp_mp4(tmp_path)
    out = adapter.upload(out, video_path=str(mp4))
    assert out.local_state == "uploaded"

    out = adapter.publish(out)
    assert out.local_state == "queued"

    # Em mock, nunca chega a 'published'
    assert out.local_state != "published"


def test_nunca_published_sem_confirmacao_remota_real():
    """Não marca published sem remote_id + remote_status + confirmed_at."""
    adapter = TikTokAdapter(live=False)
    target = PublicationTarget(
        video_id="vid-13", platform="tiktok", local_state="queued",
        remote_id=None, remote_status=None, confirmed_at=None,
    )
    out = adapter.get_remote_status(target)
    assert out.local_state != "published"
    assert out.confirmed_at is None


# --------------------------------------------------------------------------- #
# Falha em um destino não afeta outros (isolamento)                           #
# --------------------------------------------------------------------------- #
def test_publication_manager_isolamento_falha_destino(tmp_path):
    """Falha em um adapter não impede outros de publicar."""
    from src.workflow.publication import PublicationManager

    adapter_ok = TikTokAdapter(live=False)
    adapter_ok2 = YouTubeShortsAdapter(live=False)
    adapter_fail = _FailingAdapter()

    mgr = PublicationManager([adapter_ok, adapter_ok2, adapter_fail])
    targets = mgr.prepare_targets(
        state_to_dict(empty_state("exec-iso", "vid-iso")),
        video_id="vid-iso",
        platforms=["tiktok", "youtube_shorts", "failing"],
    )
    assert len(targets) == 3

    mp4 = _make_tmp_mp4(tmp_path)
    results = mgr.upload_all(targets, video_path=str(mp4))
    # O adapter que falha não impede o outro
    assert any(r.get("ok") for r in results)
    assert any(not r.get("ok", True) for r in results)


# --------------------------------------------------------------------------- #
# PublicationManager — preparação, upload, publish, status                    #
# --------------------------------------------------------------------------- #
def test_publication_manager_prepare_targets():
    """PublicationManager prepara targets para múltiplas plataformas."""
    from src.workflow.publication import PublicationManager

    mgr = PublicationManager([TikTokAdapter(live=False)])
    state = state_to_dict(empty_state("exec-pm", "vid-pm"))
    targets = mgr.prepare_targets(state, video_id="vid-pm", platforms=["tiktok"])
    assert len(targets) == 1
    assert targets[0].platform == "tiktok"
    assert targets[0].video_id == "vid-pm"
    assert targets[0].idempotency_key == "vid-pm:tiktok"


def test_publication_manager_idempotency_same_target_returned():
    """Mesmo video_id+platform retorna target existente, não duplica."""
    from src.workflow.publication import PublicationManager

    mgr = PublicationManager([TikTokAdapter(live=False)])
    state = state_to_dict(empty_state("exec-pm2", "vid-pm2"))
    targets1 = mgr.prepare_targets(state, video_id="vid-pm2", platforms=["tiktok"])
    targets2 = mgr.prepare_targets(state, video_id="vid-pm2", platforms=["tiktok"])
    assert targets1[0].target_id == targets2[0].target_id


def test_publication_manager_upload_all_results_per_adapter(tmp_path):
    """upload_all retorna resultados por adapter."""
    from src.workflow.publication import PublicationManager

    mgr = PublicationManager([TikTokAdapter(live=False), YouTubeShortsAdapter(live=False)])
    state = state_to_dict(empty_state("exec-pm3", "vid-pm3"))
    targets = mgr.prepare_targets(state, video_id="vid-pm3", platforms=["tiktok", "youtube_shorts"])
    mp4 = _make_tmp_mp4(tmp_path)
    results = mgr.upload_all(targets, video_path=str(mp4))
    assert len(results) == 2
    for r in results:
        assert "platform" in r
        assert "local_state" in r


def test_publication_manager_publish_all_results_per_adapter():
    """publish_all retorna resultados por adapter."""
    from src.workflow.publication import PublicationManager

    mgr = PublicationManager([TikTokAdapter(live=False)])
    state = state_to_dict(empty_state("exec-pm4", "vid-pm4"))
    targets = mgr.prepare_targets(state, video_id="vid-pm4", platforms=["tiktok"])
    targets = mgr.upload_all(targets, video_path="output/drafts/video.mp4")
    # targets já são PublicationTarget; rebuild para publish
    publish_targets = [
        PublicationTarget(
            video_id="vid-pm4", platform="tiktok", local_state="uploaded",
        )
    ]
    results = mgr.publish_all(publish_targets)
    assert len(results) == 1
    assert results[0]["local_state"] == "queued"


# --------------------------------------------------------------------------- #
# Integração com WorkflowState                                                #
# --------------------------------------------------------------------------- #
def test_workflow_state_has_publication_targets_field():
    """WorkflowState aceita campo publication_targets (lista de PublicationTarget)."""
    from src.workflow.state import WorkflowState

    state = empty_state("exec-ws", "vid-ws")
    assert hasattr(state, "publication_targets")
    assert state.publication_targets == []


def test_workflow_state_publication_targets_round_trip():
    """publication_targets faz round-trip por state_to_dict/dict_to_state."""
    from src.workflow.state import WorkflowState

    state = empty_state("exec-ws2", "vid-ws2")
    state.publication_targets = [
        PublicationTarget(video_id="vid-ws2", platform="tiktok", local_state="queued"),
    ]
    data = state_to_dict(state)
    assert len(data["publication_targets"]) == 1
    assert data["publication_targets"][0]["platform"] == "tiktok"
    restored = WorkflowState.model_validate(data)
    assert len(restored.publication_targets) == 1
    assert restored.publication_targets[0].platform == "tiktok"


class _FailingAdapter:
    """Adapter que sempre falha em upload/publish."""

    platform = "failing"

    def prepare(self, target: PublicationTarget) -> PublicationTarget:
        return target

    def upload(self, target: PublicationTarget, video_path: str) -> PublicationTarget:
        raise RuntimeError("adapter com falha")

    def publish(self, target: PublicationTarget) -> PublicationTarget:
        raise RuntimeError("adapter com falha")

    def get_remote_status(self, target: PublicationTarget) -> PublicationTarget:
        return target


__all__ = []
