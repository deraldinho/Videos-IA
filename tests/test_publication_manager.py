"""Testes de cobertura para src/workflow/publication.py (CP24).

Todos os testes usam adaptadores em modo mock (live=False): nenhuma chamada de
rede real, nenhuma API externa, nenhum credencial. Estados locais e remotos
permanecem distinguíveis (uploaded != queued != published).
"""

from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from src.errors import PipelineError
from src.integrations.buffer_client import BufferClient
from src.integrations.cloudinary_client import CloudinaryClient, UploadResult
from src.workflow.publication import (
    InstagramReelsAdapter,
    PublicationManager,
    PublicationTarget,
    TikTokAdapter,
    YouTubeShortsAdapter,
)
from src.workflow.state import ApprovalStatus, PublicationStatus


def _target(video_id: str, platform: str, local_state: str = "none") -> PublicationTarget:
    return PublicationTarget(
        video_id=video_id,
        platform=platform,
        channel_id="ch-tiktok",
        idempotency_key=f"{video_id}:{platform}",
        local_state=local_state,
    )


# --------------------------------------------------------------------------- #
# TikTokAdapter (mock)                                                        #
# --------------------------------------------------------------------------- #
def test_tiktok_adapter_prepare_sets_idempotency_key():
    adapter = TikTokAdapter(live=False)
    target = _target("video-1", "tiktok")
    out = adapter.prepare(target)
    assert out.idempotency_key == "video-1:tiktok"
    assert out.local_state == "none"
    assert out.error_message is None


def test_tiktok_adapter_prepare_requires_video_id():
    adapter = TikTokAdapter(live=False)
    target = PublicationTarget(platform="tiktok")
    out = adapter.prepare(target)
    assert out.error_message == "video_id é obrigatório"
    assert out.local_state == "none"


def test_tiktok_adapter_upload_mock_sets_uploaded(tmp_path: Path):
    adapter = TikTokAdapter(live=False)
    target = _target("video-1", "tiktok")
    target.local_state = "none"
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fh:
        fh.write(b"\x00\x00\x00\x00" * 1000)
        tmp = Path(fh.name)
    try:
        out = adapter.upload(target, str(tmp))
    finally:
        tmp.unlink(missing_ok=True)
    assert out.local_state == "uploaded"
    assert out.cloudinary_id is not None
    assert out.cloudinary_url is not None
    assert out.error_message is None


def test_tiktok_adapter_publish_requires_uploaded():
    adapter = TikTokAdapter(live=False)
    target = _target("video-1", "tiktok")
    # estado none != uploaded -> bloqueado
    out = adapter.publish(target)
    assert out.local_state == "none"
    assert out.error_message is not None
    assert "requer 'uploaded'" in out.error_message


def test_tiktok_adapter_publish_mock_sets_queued(tmp_path: Path):
    adapter = TikTokAdapter(live=False)
    target = _target("video-1", "tiktok")
    target.local_state = "uploaded"
    target.cloudinary_url = "https://res.cloudinary.com/demo/video/upload/v1/video-1.mp4"
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fh:
        fh.write(b"\x00\x00\x00\x00" * 1000)
        tmp = Path(fh.name)
    try:
        # upload primeiro (mock cloudinary)
        out = adapter.upload(target, str(tmp))
    finally:
        tmp.unlink(missing_ok=True)
    assert out.local_state == "uploaded"
    out = adapter.publish(out)
    assert out.local_state == "queued"
    assert out.buffer_post_id is not None
    assert out.buffer_post_state is not None


def test_tiktok_adapter_get_remote_status_mock_never_published():
    adapter = TikTokAdapter(live=False)
    target = _target("video-1", "tiktok")
    target.local_state = "queued"
    out = adapter.get_remote_status(target)
    # Mock nunca confirma publicado sem dados remotos reais
    assert out.local_state == "queued"
    assert out.publication_confirmed_at is None
    assert out.remote_status == "scheduled"


def test_tiktok_adapter_idempotent_upload_same_public_id():
    adapter = TikTokAdapter(live=False)
    target = _target("video-1", "tiktok")
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fh:
        fh.write(b"\x00\x00\x00\x00" * 1000)
        tmp = Path(fh.name)
    try:
        out1 = adapter.upload(target, str(tmp))
        out2 = adapter.upload(target, str(tmp))  # mesmo video_id -> idempotente
    finally:
        tmp.unlink(missing_ok=True)
    assert out1.cloudinary_id == out2.cloudinary_id
    assert out1.local_state == out2.local_state == "uploaded"


# --------------------------------------------------------------------------- #
# YouTubeShortsAdapter (mock)                                                 #
# --------------------------------------------------------------------------- #
def test_youtube_shorts_adapter_upload_and_publish():
    adapter = YouTubeShortsAdapter(live=False)
    target = _target("video-1", "youtube_shorts")
    target.local_state = "none"
    out = adapter.upload(target, "qualquer.mp4")
    assert out.local_state == "uploaded"
    out = adapter.publish(out)
    assert out.local_state == "queued"
    out = adapter.get_remote_status(out)
    assert out.remote_status == "scheduled"


# --------------------------------------------------------------------------- #
# InstagramReelsAdapter (mock, condicional)                                   #
# --------------------------------------------------------------------------- #
def test_instagram_reels_adapter_upload_and_publish():
    adapter = InstagramReelsAdapter(live=False)
    target = _target("video-1", "instagram_reels")
    target.local_state = "none"
    out = adapter.upload(target, "qualquer.mp4")
    assert out.local_state == "uploaded"
    out = adapter.publish(out)
    assert out.local_state == "queued"


# --------------------------------------------------------------------------- #
# PublicationManager                                                          #
# --------------------------------------------------------------------------- #
def test_manager_prepare_targets_idempotente():
    mgr = PublicationManager(
        adapters=[TikTokAdapter(live=False), YouTubeShortsAdapter(live=False)]
    )
    state: dict = {}
    t1 = mgr.prepare_targets(state, "video-1", ["tiktok", "youtube_shorts"])
    assert len(t1) == 2
    assert {t.platform for t in t1} == {"tiktok", "youtube_shorts"}
    # Segundo chamamento com mesma chave nao duplica
    t2 = mgr.prepare_targets(state, "video-1", ["tiktok", "youtube_shorts"])
    assert len(state["publication_targets"]) == 2
    assert t2[0].idempotency_key == t1[0].idempotency_key


def test_manager_prepare_targets_ignora_plataforma_sem_adapter():
    mgr = PublicationManager(adapters=[TikTokAdapter(live=False)])
    state: dict = {}
    targets = mgr.prepare_targets(state, "video-1", ["tiktok", "plataforma-inexistente"])
    assert len(targets) == 1
    assert targets[0].platform == "tiktok"


def test_manager_upload_all_isolamento_de_falha(tmp_path: Path):
    mgr = PublicationManager(adapters=[TikTokAdapter(live=False)])
    state: dict = {}
    targets = mgr.prepare_targets(state, "video-1", ["tiktok"])
    # primeiro alvo vai para uploaded
    targets[0].local_state = "uploaded"
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00\x00\x00\x00" * 1000)
    results = mgr.upload_all(targets, str(video))
    assert len(results) == 1
    assert results[0]["ok"] is True
    assert results[0]["local_state"] == "uploaded"


def test_manager_upload_all_adapter_desconhecido():
    mgr = PublicationManager(adapters=[TikTokAdapter(live=False)])
    target = _target("video-1", "plataforma-inexistente")
    results = mgr.upload_all([target], "qualquer.mp4")
    assert results[0]["ok"] is False
    assert "Nenhum adapter" in results[0]["error"]


def test_manager_publish_all_transicao_queued():
    mgr = PublicationManager(adapters=[TikTokAdapter(live=False)])
    target = _target("video-1", "tiktok")
    target.local_state = "uploaded"
    target.cloudinary_url = "https://res.cloudinary.com/demo/video/upload/v1/video-1.mp4"
    results = mgr.publish_all([target])
    assert results[0]["ok"] is True
    assert results[0]["local_state"] == "queued"
    assert results[0]["buffer_post_id"] is not None


def test_manager_publish_all_bloqueado_sem_upload():
    mgr = PublicationManager(adapters=[TikTokAdapter(live=False)])
    target = _target("video-1", "tiktok")
    # local_state none != uploaded
    results = mgr.publish_all([target])
    assert results[0]["ok"] is False
    assert results[0]["local_state"] == "none"
    assert "requer 'uploaded'" in results[0]["error"]


def test_manager_get_all_remote_status_mock_nao_confirma():
    mgr = PublicationManager(adapters=[TikTokAdapter(live=False)])
    target = _target("video-1", "tiktok")
    target.local_state = "queued"
    results = mgr.get_all_remote_status([target])
    assert results[0]["local_state"] == "queued"
    assert results[0]["remote_status"] == "scheduled"
    assert results[0]["confirmed_at"] is None


def test_manager_plataformas_multiplas(tmp_path: Path):
    mgr = PublicationManager(
        adapters=[
            TikTokAdapter(live=False),
            YouTubeShortsAdapter(live=False),
            InstagramReelsAdapter(live=False),
        ]
    )
    state: dict = {}
    targets = mgr.prepare_targets(state, "video-1", ["tiktok", "youtube_shorts", "instagram_reels"])
    assert len(targets) == 3
    assert {t.platform for t in targets} == {"tiktok", "youtube_shorts", "instagram_reels"}
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00\x00\x00\x00" * 1000)
    results = mgr.upload_all(targets, str(video))
    assert all(r["ok"] for r in results)


# --------------------------------------------------------------------------- #
# Caminhos de excecao / falha                                                 #
# --------------------------------------------------------------------------- #
def test_tiktok_upload_falha_marca_failed(tmp_path: Path):
    adapter = TikTokAdapter(live=False)
    # Injeta um cloudinary que levanta direto no adapter (evita monkeypatch de modulo)
    class _BoomCloud:
        def upload(self, *a, **k):
            raise PipelineError("boom cloudinary")

    adapter._cloudinary = _BoomCloud()
    target = _target("video-1", "tiktok")
    target.local_state = "none"
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * 1000)
    out = adapter.upload(target, str(video))
    assert out.local_state == "failed"
    assert "boom cloudinary" in out.error_message


def test_tiktok_publish_falha_marca_failed(tmp_path: Path):
    adapter = TikTokAdapter(live=False)
    class _BoomBuffer:
        def __init__(self, *a, **k):
            pass

        def get_scheduled_post_by_video(self, *a, **k):
            raise RuntimeError("boom buffer")

        def create_post(self, *a, **k):
            raise RuntimeError("boom buffer")

    adapter._buffer = _BoomBuffer()
    target = _target("video-1", "tiktok")
    target.local_state = "uploaded"
    target.cloudinary_url = "https://res.cloudinary.com/demo/video/upload/v1/video-1.mp4"
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * 1000)
    out = adapter.upload(target, str(video))
    assert out.local_state == "uploaded"
    out = adapter.publish(out)
    assert out.local_state == "failed"
    assert "boom buffer" in out.error_message


def test_youtube_shorts_prepare_requires_video_id():
    adapter = YouTubeShortsAdapter(live=False)
    target = PublicationTarget(platform="youtube_shorts")
    out = adapter.prepare(target)
    assert out.error_message == "video_id é obrigatório"
    assert out.local_state == "none"


def test_youtube_shorts_publish_bloqueado_sem_upload():
    adapter = YouTubeShortsAdapter(live=False)
    target = _target("video-1", "youtube_shorts")
    out = adapter.publish(target)
    assert out.local_state == "none"
    assert out.error_message is not None


def test_instagram_prepare_requires_video_id():
    adapter = InstagramReelsAdapter(live=False)
    target = PublicationTarget(platform="instagram_reels")
    out = adapter.prepare(target)
    assert out.error_message == "video_id é obrigatório"


def test_instagram_publish_bloqueado_sem_upload():
    adapter = InstagramReelsAdapter(live=False)
    target = _target("video-1", "instagram_reels")
    out = adapter.publish(target)
    assert out.local_state == "none"
    assert out.error_message is not None


def test_tiktok_get_remote_status_live_sem_remote_client():
    adapter = TikTokAdapter(live=True)  # live mas sem _remote_client injetado
    target = _target("video-1", "tiktok")
    out = adapter.get_remote_status(target)
    assert out.error_message is not None
    assert "remote_client" in out.error_message or "credenciais" in out.error_message
    assert out.local_state == "none"


# --------------------------------------------------------------------------- #
# PublicationManager: falha por adapter nao encontrado                        #
# --------------------------------------------------------------------------- #
def test_manager_upload_all_adapter_desconhecido():
    mgr = PublicationManager(adapters=[TikTokAdapter(live=False)])
    target = _target("video-1", "plataforma-inexistente")
    results = mgr.upload_all([target], "qualquer.mp4")
    assert results[0]["ok"] is False
    assert "Nenhum adapter" in results[0]["error"]


def test_manager_publish_all_adapter_desconhecido():
    mgr = PublicationManager(adapters=[TikTokAdapter(live=False)])
    target = _target("video-1", "plataforma-inexistente")
    results = mgr.publish_all([target])
    assert results[0]["ok"] is False
    assert "Nenhum adapter" in results[0]["error"]


def test_manager_get_all_remote_status_adapter_desconhecido():
    mgr = PublicationManager(adapters=[TikTokAdapter(live=False)])
    target = _target("video-1", "plataforma-inexistente")
    results = mgr.get_all_remote_status([target])
    assert results[0]["error"] == "Nenhum adapter"
    assert results[0]["local_state"] == "none"


def test_manager_publish_all_isolamento_de_falha(tmp_path: Path):
    # Um adapter falha no publish (buffer boom), o alvo vai para failed
    class _BoomBuffer:
        def __init__(self, *a, **k):
            pass

        def get_scheduled_post_by_video(self, *a, **k):
            raise RuntimeError("boom")

        def create_post(self, *a, **k):
            raise RuntimeError("boom")

    adapter = TikTokAdapter(live=False)
    adapter._buffer = _BoomBuffer()
    mgr = PublicationManager(adapters=[adapter])
    state: dict = {}
    targets = mgr.prepare_targets(state, "video-1", ["tiktok"])
    targets[0].local_state = "uploaded"
    targets[0].cloudinary_url = "https://res.cloudinary.com/demo/video/upload/v1/video-1.mp4"
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * 1000)
    mgr.upload_all(targets, str(video))
    results = mgr.publish_all(targets)
    assert results[0]["ok"] is False
    assert results[0]["local_state"] == "failed"


# --------------------------------------------------------------------------- #
# Extras de cobertura (linhas remanescentes de publication.py)               #
# --------------------------------------------------------------------------- #
def test_tiktok_upload_rejeita_video_imenso(tmp_path: Path):
    adapter = TikTokAdapter(live=False)
    from src.integrations.cloudinary_client import CloudinaryClient
    adapter._cloudinary = CloudinaryClient(cloud_name="demo", live=False, max_video_mb=1)
    target = _target("video-1", "tiktok")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * (2 * 1024 * 1024))  # 2MB > 1MB
    out = adapter.upload(target, str(video))
    assert out.local_state == "failed"
    assert "excede" in out.error_message


def test_tiktok_publish_idempotencia_existing_post():
    # Buffer ja tem post para o video_id -> reaproveita post_id existente
    adapter = TikTokAdapter(live=False)

    class _BufferExisting:
        def __init__(self, *a, **k):
            pass

        def get_scheduled_post_by_video(self, video_id):
            class _Post:
                post_id = "post-existente"
                status = "scheduled"

            return _Post()

    adapter._buffer = _BufferExisting()
    target = _target("video-1", "tiktok")
    target.local_state = "uploaded"
    target.cloudinary_url = "https://res.cloudinary.com/demo/video/upload/v1/video-1.mp4"
    out = adapter.publish(target)
    assert out.local_state == "queued"
    assert out.buffer_post_id == "post-existente"


def test_tiktok_get_remote_status_live_confirma_publicado():
    # Live com remote_client que confirma publicado -> local_state='published'
    adapter = TikTokAdapter(live=True)

    class _Remote:
        def get_video_status(self, video_id):
            return {
                "published": True,
                "remote_id": "remote-42",
                "remote_status": "published",
                "confirmed_at": "2026-01-01T00:00:00+00:00",
            }

    adapter._remote_client = _Remote()
    target = _target("video-1", "tiktok")
    target.local_state = "queued"
    out = adapter.get_remote_status(target)
    assert out.local_state == "published"
    assert out.remote_id == "remote-42"
    assert out.publication_confirmed_at is not None


def test_tiktok_get_remote_status_live_nao_confirma_sem_published():
    # Live com remote_client que retorna published=False -> nao marca publicado
    adapter = TikTokAdapter(live=True)

    class _RemotePending:
        def get_video_status(self, video_id):
            return {
                "published": False,
                "remote_status": "pending",
            }

    adapter._remote_client = _RemotePending()
    target = _target("video-1", "tiktok")
    target.local_state = "queued"
    out = adapter.get_remote_status(target)
    assert out.local_state == "queued"
    assert out.remote_status == "pending"
    assert out.publication_confirmed_at is None
    assert "nao confirmado" in out.error_message or "não confirmado" in out.error_message


def test_youtube_shorts_get_remote_status():
    adapter = YouTubeShortsAdapter(live=False)
    target = _target("video-1", "youtube_shorts")
    out = adapter.get_remote_status(target)
    assert out.remote_status == "scheduled"


def test_instagram_get_remote_status():
    adapter = InstagramReelsAdapter(live=False)
    target = _target("video-1", "instagram_reels")
    out = adapter.get_remote_status(target)
    assert out.remote_status == "scheduled"


# --------------------------------------------------------------------------- #
# Linhas remanescentes de publication.py (excecoes e caminhos default)       #
# --------------------------------------------------------------------------- #
def test_tiktok_prepare_define_idempotency_key_quando_ausente():
    # Linha 70: idempotency_key ja existe -> pula definicao; aqui testa o default quando ausente
    adapter = TikTokAdapter(live=False)
    target = PublicationTarget(video_id="video-1", platform="tiktok")  # sem idempotency_key
    out = adapter.prepare(target)
    assert out.idempotency_key == "video-1:tiktok"


def test_youtube_shorts_prepare_define_idempotency_key_quando_ausente():
    # Linha 204
    adapter = YouTubeShortsAdapter(live=False)
    target = PublicationTarget(video_id="video-1", platform="youtube_shorts")
    out = adapter.prepare(target)
    assert out.idempotency_key == "video-1:youtube_shorts"


def test_instagram_prepare_define_idempotency_key_quando_ausente():
    # Linha 247
    adapter = InstagramReelsAdapter(live=False)
    target = PublicationTarget(video_id="video-1", platform="instagram_reels")
    out = adapter.prepare(target)
    assert out.idempotency_key == "video-1:instagram_reels"


def test_tiktok_get_remote_status_live_erro_consulta():
    # Linhas 180-181: excecao ao consultar remote_client
    adapter = TikTokAdapter(live=True)

    class _RemoteBoom:
        def get_video_status(self, video_id):
            raise RuntimeError("rede caiu")

    adapter._remote_client = _RemoteBoom()
    target = _target("video-1", "tiktok")
    target.local_state = "queued"
    out = adapter.get_remote_status(target)
    assert out.error_message is not None
    assert "Falha ao consultar status remoto" in out.error_message


def test_manager_upload_all_adapter_levanta_excecao(tmp_path: Path):
    # Linhas 360-361: except dentro de upload_all
    class _BoomAdapter:
        platform = "boom"

        def upload(self, target, video_path):
            raise RuntimeError("boom upload")

    mgr = PublicationManager(adapters=[_BoomAdapter()])
    target = _target("video-1", "boom")
    target.local_state = "none"
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * 1000)
    results = mgr.upload_all([target], str(video))
    assert results[0]["ok"] is False
    assert "boom upload" in results[0]["error"]


def test_manager_publish_all_adapter_levanta_excecao():
    # Linhas 397-398: except dentro de publish_all
    class _BoomAdapter:
        platform = "boom"

        def publish(self, target):
            raise RuntimeError("boom publish")

    mgr = PublicationManager(adapters=[_BoomAdapter()])
    target = _target("video-1", "boom")
    target.local_state = "uploaded"
    results = mgr.publish_all([target])
    assert results[0]["ok"] is False
    assert "boom publish" in results[0]["error"]


def test_manager_get_all_remote_status_adapter_levanta_excecao():
    # Linhas 435-436: except dentro de get_all_remote_status
    class _BoomAdapter:
        platform = "boom"

        def get_remote_status(self, target):
            raise RuntimeError("boom status")

    mgr = PublicationManager(adapters=[_BoomAdapter()])
    target = _target("video-1", "boom")
    results = mgr.get_all_remote_status([target])
    assert results[0]["error"] == "boom status"


__all__ = []