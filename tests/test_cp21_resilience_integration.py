"""CP21 — Integração de circuit breaker + retry nas chamadas de serviços externos.

CP11 entregou os componentes (src/circuit_breaker.py, src/retry.py) mas nunca os
conectou ao pipeline: o Orchestrator chamava CloudinaryClient.upload e
BufferClient.create_post diretamente, sem proteção contra falhas transitórias de
rede nem contra cascata de falhas de serviços externos.

Este ciclo (CP21) integra circuit breaker + retry no Orchestrator em torno das
duas chamadas externas (_stage_upload e _stage_publish) via o helper privado
`_call_with_resilience`.

Em modo não-live (mock) as chamadas nunca falham, portanto o circuito nunca abre
e o retry não é acionado: comportamento idêntico ao de antes (sem regressão).
Em modo live, falhas transitórias são retryadas com backoff e, após o
threshold, o circuito abre bloqueando novas chamadas.

Os testes abaixo exercitam `_call_with_resilience` diretamente (a unidade de
resiliência recém-integrada), evitando depender do estado da máquina de estados
nem de campos pré-existentes do banco (ex.: `script_json` armazenado como
string).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from src.errors import PipelineError
from src.integrations.buffer_client import PostResult
from src.integrations.cloudinary_client import UploadResult
from src.orchestrator import Orchestrator


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _new_orch(tmp_path: Path) -> Orchestrator:
    return Orchestrator(db_path=tmp_path / "orch.sqlite3")


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.retry as retry_module

    monkeypatch.setattr(retry_module.time, "sleep", lambda *a, **k: None)


# --------------------------------------------------------------------------- #
# 1. Circuit breakers são inicializados no Orchestrator                       #
# --------------------------------------------------------------------------- #
def test_orchestrator_initializes_circuit_breakers(tmp_path: Path) -> None:
    orch = _new_orch(tmp_path)
    try:
        assert isinstance(orch._cloudinary_cb, CircuitBreaker)
        assert isinstance(orch._buffer_cb, CircuitBreaker)
        assert orch._cloudinary_cb.name == "cloudinary"
        assert orch._buffer_cb.name == "buffer"
        assert orch._cloudinary_cb.state.value == "closed"
        assert orch._buffer_cb.state.value == "closed"
    finally:
        orch.close()


# --------------------------------------------------------------------------- #
# 2. Cloudinary: falha transitória é retryada até sucesso                     #
# --------------------------------------------------------------------------- #
def test_cloudinary_call_retries_transient_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _new_orch(tmp_path)
    try:
        calls = {"n": 0}

        def flaky_upload(video_id, file_path):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PipelineError("falha transitória de rede")
            return UploadResult(
                public_id="tiktok_automation/v1/abc",
                secure_url="https://res.cloudinary.com/demo/video/upload/v1/abc.mp4",
                url="http://res.cloudinary.com/demo/video/upload/v1/abc.mp4",
                bytes_uploaded=100,
                duration_seconds=5.0,
                mock=False,
            )

        monkeypatch.setattr(orch.cloudinary, "upload", flaky_upload)
        _no_sleep(monkeypatch)

        result = orch._call_with_resilience(
            orch._cloudinary_cb, orch.cloudinary.upload, "v1", Path("x.mp4")
        )
        assert isinstance(result, UploadResult)
        assert result.public_id == "tiktok_automation/v1/abc"
        # 3 chamadas ao upload (2 falhas + 1 sucesso).
        assert calls["n"] == 3, calls
        # Circuito fechado novamente após sucesso.
        assert orch._cloudinary_cb.state.value == "closed"
    finally:
        orch.close()


# --------------------------------------------------------------------------- #
# 3. Cloudinary: falha persistente abre o circuito e bloqueia                 #
# --------------------------------------------------------------------------- #
def test_cloudinary_call_persistent_failure_opens_circuit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _new_orch(tmp_path)
    try:
        def always_fails(video_id, file_path):
            raise PipelineError("sempre falha")

        monkeypatch.setattr(orch.cloudinary, "upload", always_fails)
        _no_sleep(monkeypatch)

        # Primeira chamada: esgota retry (3 tentativas), levanta PipelineError.
        # Após isso o circuito abre (fail_threshold=3).
        with pytest.raises(PipelineError, match="sempre falha"):
            orch._call_with_resilience(
                orch._cloudinary_cb, orch.cloudinary.upload, "v1", Path("x.mp4")
            )
        assert orch._cloudinary_cb.state.value == "open"

        # Segunda chamada: circuito aberto -> bloqueia imediatamente.
        with pytest.raises(CircuitBreakerOpenError):
            orch._call_with_resilience(
                orch._cloudinary_cb, orch.cloudinary.upload, "v1", Path("x.mp4")
            )
        assert orch._cloudinary_cb.state.value == "open"
    finally:
        orch.close()


# --------------------------------------------------------------------------- #
# 4. Buffer: falha transitória é retryada até sucesso                         #
# --------------------------------------------------------------------------- #
def test_buffer_call_retries_transient_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _new_orch(tmp_path)
    try:
        calls = {"n": 0}

        def flaky_create_post(**kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PipelineError("falha transitória no Buffer")
            return PostResult(
                post_id=f"bp-{calls['n']}",
                status="scheduled",
                channel_id="ch-tiktok",
                text="legenda",
                due_at=None,
                mock=False,
            )

        monkeypatch.setattr(orch.buffer, "create_post", flaky_create_post)
        _no_sleep(monkeypatch)

        result = orch._call_with_resilience(
            orch._buffer_cb,
            orch.buffer.create_post,
            video_id="v1",
            text="legenda",
            channel_id="ch-tiktok",
            due_at=None,
            video_url=None,
        )
        assert isinstance(result, PostResult)
        assert result.post_id == "bp-3"
        assert calls["n"] == 3, calls
        assert orch._buffer_cb.state.value == "closed"
    finally:
        orch.close()


# --------------------------------------------------------------------------- #
# 5. Buffer: falha persistente abre o circuito e bloqueia                     #
# --------------------------------------------------------------------------- #
def test_buffer_call_persistent_failure_opens_circuit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _new_orch(tmp_path)
    try:
        def always_fails(**kwargs):
            raise PipelineError("sempre falha")

        monkeypatch.setattr(orch.buffer, "create_post", always_fails)
        _no_sleep(monkeypatch)

        with pytest.raises(PipelineError, match="sempre falha"):
            orch._call_with_resilience(
                orch._buffer_cb,
                orch.buffer.create_post,
                video_id="v1", text="t", channel_id="c", due_at=None,
                video_url=None,
            )
        assert orch._buffer_cb.state.value == "open"

        with pytest.raises(CircuitBreakerOpenError):
            orch._call_with_resilience(
                orch._buffer_cb,
                orch.buffer.create_post,
                video_id="v1", text="t", channel_id="c", due_at=None,
                video_url=None,
            )
        assert orch._buffer_cb.state.value == "open"
    finally:
        orch.close()


# --------------------------------------------------------------------------- #
# 6. Upload de ponta a ponta com retry (invoque real de _stage_upload)        #
# --------------------------------------------------------------------------- #
def test_upload_stage_retries_then_reaches_uploaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vídeo em APPROVED + MP4 existente: upload com retry chega a UPLOADED."""
    settings = Orchestrator().settings  # REVIEW, não-live
    orch = Orchestrator(settings=settings, db_path=tmp_path / "v.sqlite3")
    try:
        orch.db.create_video(
            video_id="v1", execution_id="exec-1", topic="t",
            template="curiosidade", kind="factual", title="T", description="D",
            hashtags=[], script_json={}, total_seconds=15.0,
        )
        for to in ("SCRIPT_READY", "RENDERING"):
            orch.sm.advance("v1", to)
        mp4 = tmp_path / "v1.mp4"
        mp4.write_bytes(b"\x00\x00\x00\x00")
        orch.sm.advance("v1", "RENDERED", mp4_path=str(mp4))
        orch.sm.advance("v1", "VALIDATED")
        orch.sm.advance("v1", "AWAITING_APPROVAL")
        orch.sm.advance("v1", "APPROVED")

        calls = {"n": 0}

        def flaky_upload(video_id, file_path):
            calls["n"] += 1
            if calls["n"] < 3:
                raise PipelineError("falha transitória de rede")
            return UploadResult(
                public_id="tiktok_automation/v1/abc",
                secure_url="https://res.cloudinary.com/demo/video/upload/v1/abc.mp4",
                url="http://res.cloudinary.com/demo/video/upload/v1/abc.mp4",
                bytes_uploaded=100,
                duration_seconds=5.0,
                mock=False,
            )

        monkeypatch.setattr(orch.cloudinary, "upload", flaky_upload)
        _no_sleep(monkeypatch)

        res = orch.run_stage("v1", "upload")
        assert res.status == "UPLOADED", res.note
        assert calls["n"] == 3, calls
        assert orch.db.get_video("v1")["status"] == "UPLOADED"
        assert orch._cloudinary_cb.state.value == "closed"
    finally:
        orch.close()


__all__ = []
