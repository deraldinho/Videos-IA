"""Testes de regressão para os defeitos P0 e P1 (CP02).

Cobre:
- Transicao deterministica APPROVED -> UPLOADING -> UPLOADED (nunca salta para UPLOADED).
- Protecao contra upload/publicacao sem autorizacao (estado APPROVED obrigatorio).
- run_pipeline(up_to=) usa comparacao numerica de indice (nao alfabetica).
- Idempotencia persistente do Buffer (reuso apos reinicio).
- Upload do Cloudinary (modo live) envia bytes do arquivo, nao string de caminho.
- Substituicao de variaveis {{ }} nos templates HTML.
- MP4 de saida contem stream de áudio (narracao TTS incorporada / silencio fallback).
- Validador strict rejeita fallback preto/silencioso.
- Comando `tick` registrado no CLI.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database import Database  # noqa: E402
from src.errors import PipelineError  # noqa: E402
from src.integrations.buffer_client import BufferClient  # noqa: E402
from src.integrations.cloudinary_client import CloudinaryClient  # noqa: E402
from src.settings import load_settings  # noqa: E402
from src.state_machine import StateMachine  # noqa: E402
from src.video.renderer import HTMLRenderer  # noqa: E402
from src.video.validator import FFprobeValidator  # noqa: E402

TEMPLATE = "curiosity"  # arquivo real: templates/curiosity.html


def _tmp_settings(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mode: REVIEW\nniche: teste\nscript_kind: factual\n"
        "video:\n  min_seconds: 30\n  max_seconds: 60\n  target_seconds: 45\n  ffmpeg: ffmpeg\n  ffprobe: ffprobe\n  node: node\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    return load_settings(cfg, env)


def _make_video(db: Database, video_id: str, execution_id: str, status_fields=None):
    db.create_video(
        video_id=video_id,
        execution_id=execution_id,
        topic="t",
        template=TEMPLATE,
        kind="factual",
        title="T",
        description="D",
        hashtags=[],
        script_json={},
        total_seconds=10.0,
    )
    if status_fields:
        with db.transaction() as conn:
            sets = ", ".join(f"{k} = ?" for k in status_fields)
            params = list(status_fields.values()) + [video_id]
            conn.execute(f"UPDATE videos SET {sets} WHERE video_id = ?", params)


# --------------------------------------------------------------------------- #
# P0-2: Transicao deterministica APPROVED -> UPLOADING -> UPLOADED            #
# --------------------------------------------------------------------------- #
def test_state_machine_blocks_direct_approved_to_uploaded(tmp_path):
    db = Database(tmp_path / "t.sqlite3")
    sm = StateMachine(db)
    _make_video(db, "v1", "exec-1")
    # Leva o video ate APPROVED passo a passo (só transicoes permitidas).
    for to in ("SCRIPT_READY", "RENDERING", "RENDERED", "VALIDATED",
               "AWAITING_APPROVAL", "APPROVED"):
        sm.advance("v1", to, mp4_path="/x/v1.mp4" if to == "RENDERED" else None)

    # Salto direto APPROVED -> UPLOADED deve ser bloqueado.
    with pytest.raises(PipelineError, match="não permitida"):
        sm.advance("v1", "UPLOADED")

    # Caminho correto: APPROVED -> UPLOADING -> UPLOADED.
    sm.advance("v1", "UPLOADING")
    assert db.get_video("v1")["status"] == "UPLOADING"
    sm.advance("v1", "UPLOADED")
    assert db.get_video("v1")["status"] == "UPLOADED"
    db.close()


# --------------------------------------------------------------------------- #
# P0-4: Protecao contra upload/publicacao sem autorizacao                     #
# --------------------------------------------------------------------------- #
def test_upload_requires_approved_state(tmp_path):
    settings = _tmp_settings(tmp_path)
    db_path = tmp_path / "v.sqlite3"
    from src.orchestrator import Orchestrator  # noqa: PLC0415
    orch = Orchestrator(settings=settings, db_path=db_path)
    try:
        # Video em IDEA_CREATED (sem aprovacao): upload deve recusar.
        orch.db.create_video(
            video_id="v2", execution_id="exec-2", topic="t", template=TEMPLATE,
            kind="factual", title="T", description="D", hashtags=[],
            script_json={}, total_seconds=10.0,
        )
        with pytest.raises(PipelineError, match="Upload requer APPROVED"):
            orch.run_stage("v2", "upload")
    finally:
        orch.close()


def _approved_video_with_mp4(tmp_path: Path):
    from src.orchestrator import Orchestrator  # noqa: PLC0415
    settings = _tmp_settings(tmp_path)
    db_path = tmp_path / "v.sqlite3"
    orch = Orchestrator(settings=settings, db_path=db_path)
    try:
        orch.db.create_video(
            video_id="v1", execution_id="exec-1", topic="t", template=TEMPLATE,
            kind="factual", title="T", description="D", hashtags=[],
            script_json={"scenes": []}, total_seconds=15.0,
        )
        for to in ("SCRIPT_READY", "RENDERING"):
            orch.sm.advance("v1", to)
        mp4 = tmp_path / "v1.mp4"
        mp4.write_bytes(b"\x00\x00\x00\x00")
        orch.sm.advance("v1", "RENDERED", mp4_path=str(mp4))
        orch.sm.advance("v1", "VALIDATED")
        orch.sm.advance("v1", "AWAITING_APPROVAL")
        orch.sm.advance("v1", "APPROVED")
        return orch
    finally:
        orch.close()


def test_upload_advances_through_uploading_then_uploaded(tmp_path):
    orch = _approved_video_with_mp4(tmp_path)
    try:
        res = orch.run_stage("v1", "upload")
        assert res.status == "UPLOADED"
        row = orch.db.get_video("v1")
        assert row["status"] == "UPLOADED"
        assert row["cloudinary_id"] is not None
        assert row["cloudinary_url"] is not None
        # Reexecutar e idempotente (ja em UPLOADED).
        res2 = orch.run_stage("v1", "upload")
        assert res2.changed is False
        assert orch.db.get_video("v1")["status"] == "UPLOADED"
    finally:
        orch.close()


# --------------------------------------------------------------------------- #
# P0-3: run_pipeline(up_to=) usa indice numerico (nao alfabetico)             #
# --------------------------------------------------------------------------- #
def test_run_pipeline_up_to_stops_at_numeric_index(tmp_path):
    from src.orchestrator import Orchestrator  # noqa: PLC0415
    settings = _tmp_settings(tmp_path)
    db_path = tmp_path / "v.sqlite3"
    orch = Orchestrator(settings=settings, db_path=db_path)
    try:
        orch.db.create_video(
            video_id="v1", execution_id="exec-1", topic="t", template=TEMPLATE,
            kind="factual", title="T", description="D",
            hashtags=["#A", "#B", "#C"],
            script_json={
                "scenes": [
                    {"role": "gancho", "seconds": 3.0, "heading": "H",
                     "narration": ["Oi."], "source_ids": []},
                    {"role": "cta", "seconds": 2.0, "heading": "H",
                     "narration": ["Tchau."], "source_ids": []},
                ]
            },
            total_seconds=5.0,
        )
        orch.sm.advance("v1", "SCRIPT_READY")
        # templates_dir e relativo ao cwd; aponta para o diretorio real do projeto.
        orch.renderer.templates_dir = ROOT / "templates"

        # up_to="tts" (indice 1): executa script e tts, para antes de render (indice 2).
        results = orch.run_pipeline("v1", up_to="tts")
        statuses = [r.stage for r in results]
        assert "script" in statuses
        assert "tts" in statuses
        assert "render" not in statuses
        assert orch.db.get_video("v1")["status"] == "RENDERING"

        # Ate "render" nao deve pular para validate.
        orch2 = Orchestrator(settings=settings, db_path=db_path)
        orch2.renderer.templates_dir = ROOT / "templates"
        try:
            res = orch2.run_stage("v1", "render")
            assert res.status == "RENDERED"
        finally:
            orch2.close()
    finally:
        orch.close()


# --------------------------------------------------------------------------- #
# P1-5: Idempotencia persistente do Buffer (reuso apos reinicio)              #
# --------------------------------------------------------------------------- #
def test_buffer_idempotent_across_restart(tmp_path):
    db = Database(tmp_path / "t.sqlite3")
    _make_video(db, "v1", "exec-1")
    # Simula post ja gravado (p. apos uma execucao anterior).
    db.upsert_scheduled_post(
        video_id="v1", buffer_post_id="bp-persistente", state="scheduled",
        scheduled_at="2026-01-01T12:00:00Z", platform="tiktok",
    )

    # Novo cliente (simula reinicio do processo) com o mesmo banco e canal configurado.
    client = BufferClient(channel_id="ch-tiktok", live=False, db=db)
    p = client.create_post("v1", "qualquer texto")
    assert p.post_id == "bp-persistente"
    assert p.status == "scheduled"
    assert p.mock is True

    # Nao duplica linhas no banco.
    with db.transaction() as conn:
        count = conn.execute(
            "SELECT COUNT(*) as c FROM scheduled_posts WHERE buffer_post_id = ?",
            ("bp-persistente",),
        ).fetchone()["c"]
    assert count == 1
    db.close()


# --------------------------------------------------------------------------- #
# P1-6: Cloudinary upload (live) envia bytes do arquivo, nao caminho string   #
# --------------------------------------------------------------------------- #
def test_cloudinary_live_upload_sends_file_bytes(monkeypatch, tmp_path):
    captured = {}

    class FakeResp:
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "public_id": "tiktok_automation/v1/abc",
                "secure_url": "https://res.cloudinary.com/demo/video/upload/v1/abc.mp4",
                "url": "http://res.cloudinary.com/demo/video/upload/v1/abc.mp4",
                "bytes": 100,
                "duration": 5.0,
            }

    def fake_post(url, *, data, files, auth, timeout):
        captured["data"] = data
        captured["files"] = files
        captured["auth"] = auth
        # Le os bytes enquanto o handle ainda esta aberto (dentro do `with` do cliente).
        captured["file_bytes"] = files["file"][1].read()
        return FakeResp()

    monkeypatch.setattr("requests.post", fake_post)

    client = CloudinaryClient(cloud_name="demo", api_key="k", api_secret="s", live=True)
    tmp = tmp_path / "video.mp4"
    tmp.write_bytes(b"\x00\x00\x00\x00" * 100)

    res = client.upload("vid-1", tmp)
    assert res.mock is False
    assert res.public_id == "tiktok_automation/v1/abc"

    # 'file' NAO deve ir como string de caminho em 'data'.
    assert "file" not in captured["data"]
    # O arquivo deve ir em 'files' e seus bytes devem ser lidos (nao o caminho).
    assert "file" in captured["files"]
    assert captured["file_bytes"] == b"\x00\x00\x00\x00" * 100


# --------------------------------------------------------------------------- #
# P1-7: Variaveis HTML {{ }} substituidas no renderer                         #
# --------------------------------------------------------------------------- #
def test_renderer_substitutes_html_variables():
    html = "<h1>{{title}}</h1><p>{{description}}</p><div>{{hashtags}}</div>"
    context = {
        "title": "O primeiro site",
        "description": "Uma curiosidade.",
        "hashtags": ["#CERN", "#Web"],
    }
    out = HTMLRenderer._substitute(html, context)
    assert "{{title}}" not in out
    assert "{{description}}" not in out
    assert "{{hashtags}}" not in out
    assert "O primeiro site" in out
    assert "Uma curiosidade." in out
    assert "#CERN #Web" in out


# --------------------------------------------------------------------------- #
# P1-8: MP4 de saida contem stream de áudio (TTS / silencio)                  #
# --------------------------------------------------------------------------- #
def _write_wav(path: Path, seconds: float = 1.0) -> None:
    sample_rate = 22050
    n_samples = int(sample_rate * seconds)
    import wave  # noqa: PLC0415
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_samples)


def test_renderer_output_mp4_has_audio_stream(tmp_path):
    # Template minimo para o renderer nao falhar na verificacao de existencia.
    tmpl = tmp_path / "curiosidade.html"
    tmpl.write_text("<h1>{{title}}</h1><p>{{description}}</p>", encoding="utf-8")
    audio = tmp_path / "narracao.wav"
    _write_wav(audio, seconds=1.0)

    renderer = HTMLRenderer(
        node_bin="node", ffmpeg_bin="ffmpeg",
        templates_dir=tmp_path, output_dir=tmp_path,
    )
    out = renderer.render(
        "curiosidade", {"title": "T", "description": "D"},
        duration_seconds=1, output_name="out.mp4", audio_wav=audio,
    )
    assert out.exists()
    assert out.stat().st_size > 0

    info = FFprobeValidator().probe(out)
    assert info.width == 1080 and info.height == 1920
    assert abs(info.fps - 30.0) <= 1.0
    assert info.has_audio  # MP4 deve conter stream de áudio


# --------------------------------------------------------------------------- #
# P1-9: Validador strict rejeita fallback preto/silencioso                    #
# --------------------------------------------------------------------------- #
def test_validator_strict_rejects_silent_video(tmp_path):
    v = FFprobeValidator()
    mp4 = tmp_path / "silent.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=30:d=2",
        "-f", "lavfi", "-i", "anullsrc=r=22050:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-shortest", str(mp4),
    ], capture_output=True, check=True)

    # Nao strict: valido estruturalmente (1080x1920, 30fps, tem audio).
    info = v.validate_tiktok(mp4, strict=False)
    assert info.width == 1080 and info.height == 1920
    assert info.has_audio

    # Strict: rejeita por silencio (mean_volume bem abaixo de -60 dB).
    with pytest.raises(PipelineError, match="silencioso"):
        v.validate_tiktok(mp4, strict=True)


# --------------------------------------------------------------------------- #
# P1-10: Comando `tick` existe no CLI                                         #
# --------------------------------------------------------------------------- #
def test_tick_command_registered():
    import main  # noqa: PLC0415
    assert "tick" in main.COMMANDS
    assert main.COMMANDS["tick"] is main.cmd_tick


__all__ = []
