"""Testes de regressão — Prioridade 1: renderização real (Chromium + FFmpeg).

Valida que o pipeline produz MP4 de verdade, não placeholders pretos/silenciosos,
e que falhas de dependências externas geram erros explícitos (nunca silêncio disfarçado).
"""

from __future__ import annotations

import subprocess
import tempfile
import wave
from pathlib import Path

import pytest

from src.errors import PipelineError
from src.video.renderer import HTMLRenderer
from src.video.validator import FFprobeValidator
from src.workflow.agents.render import RenderAgent
from src.workflow.state import empty_state


def _make_wav(path: Path, seconds: float = 2.0, freq: float = 440.0) -> None:
    """Gera um WAV audível (senóide) para uso como narração."""
    import math
    sample_rate = 22050
    nframes = int(sample_rate * seconds)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        for i in range(nframes):
            value = int(0.4 * 32767 * math.sin(2 * math.pi * freq * i / sample_rate))
            wf.writeframes(value.to_bytes(2, "little", signed=True))


def _initial_state(video_id: str = "vid-render-real") -> dict:
    return {
        "execution_id": "exec-render-real",
        "video_id": video_id,
        "topic": "tema render real",
        "status": "VISUAL_READY",
        "current_stage": "VISUAL_READY",
        "mode": "REVIEW",
        "live_enabled": False,
        "auto_enabled": False,
    }


# --------------------------------------------------------------------------- #
# P1-1: Renderização real produz MP4 não-preto com áudio                      #
# --------------------------------------------------------------------------- #
def test_render_real_produces_non_black_video(tmp_path):
    """HTMLRenderer deve gerar MP4 real: 1080x1920, 30fps, H.264, áudio, não-preto."""
    renderer = HTMLRenderer(output_dir=tmp_path / "renders")
    ctx = {
        "video_id": "vid-real",
        "topic": "tema",
        "template": "curiosity",
        "title": "Titulo real",
        "description": "Descricao de validacao.",
        "hashtags": ["#ia"],
        "scenes": [
            {"role": "gancho", "seconds": 1.5, "heading": "Cena A", "narration": ["Olá"], "source_ids": []},
            {"role": "corpo", "seconds": 1.5, "heading": "Cena B", "narration": ["Mundo"], "source_ids": []},
        ],
        "duration_seconds": 3.0,
    }
    mp4 = renderer.render("curiosity", ctx, duration_seconds=3.0, output_name="real.mp4")
    assert mp4.exists()

    info = FFprobeValidator().probe(mp4)
    assert info.width == 1080 and info.height == 1920, info
    assert abs(info.fps - 30.0) <= 1.0, info
    assert info.codec == "h264", info
    assert info.has_audio, "MP4 deve conter stream de áudio"
    assert info.duration_seconds >= 2.5, info

    # Não deve ser todo preto.
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(mp4),
         "-vf", "blackdetect=d=0.5:pix_th=0.05", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )
    output = proc.stdout + "\n" + proc.stderr
    assert output.count("black_start") < 5, f"Vídeo parece placeholder preto: {output}"


# --------------------------------------------------------------------------- #
# P1-2: Avanço efetivo entre cenas (frames diferem ao longo do tempo)         #
# --------------------------------------------------------------------------- #
def test_render_advances_between_scenes(tmp_path):
    """Frames capturados em momentos diferentes devem mostrar mudança visual."""
    renderer = HTMLRenderer(output_dir=tmp_path / "renders")
    ctx = {
        "video_id": "vid-scenes",
        "template": "curiosity",
        "title": "T",
        "description": "D",
        "hashtags": [],
        "scenes": [
            {"role": "gancho", "seconds": 1.0, "heading": "CENA UM", "narration": ["um"], "source_ids": []},
            {"role": "corpo", "seconds": 1.0, "heading": "CENA DOIS", "narration": ["dois"], "source_ids": []},
        ],
        "duration_seconds": 2.0,
    }
    mp4 = renderer.render("curiosity", ctx, duration_seconds=2.0, output_name="scenes.mp4")
    assert mp4.exists()

    # Extrai dois frames (início e fim) e confirma que diferem (não é imagem estática).
    f1 = tmp_path / "f1.png"
    f2 = tmp_path / "f2.png"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(mp4), "-ss", "0.2", "-frames:v", "1", str(f1)],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(mp4), "-ss", "1.5", "-frames:v", "1", str(f2)],
        capture_output=True, check=True,
    )
    b1 = f1.read_bytes()
    b2 = f2.read_bytes()
    # O cabeçalho PNG é igual; o corpo (IDAT comprimido) difere se a imagem mudou.
    assert b1 != b2, "Frames de início e fim são idênticos (vídeo estático, sem mudança de cena)"


# --------------------------------------------------------------------------- #
# P1-3: Composição de narração TTS real (WAV audível no MP4)                  #
# --------------------------------------------------------------------------- #
def test_render_with_tts_audio_is_audible(tmp_path):
    """RenderAgent/HTMLRenderer com WAV de narração deve produzir áudio audível."""
    renderer = HTMLRenderer(output_dir=tmp_path / "renders")
    wav = tmp_path / "narracao.wav"
    _make_wav(wav, seconds=3.0, freq=440.0)

    ctx = {
        "video_id": "vid-tts",
        "template": "curiosity",
        "title": "TTS",
        "description": "D",
        "hashtags": [],
        "scenes": [{"role": "gancho", "seconds": 3.0, "heading": "Narração", "narration": ["áudio"], "source_ids": []}],
        "duration_seconds": 3.0,
    }
    mp4 = renderer.render("curiosity", ctx, duration_seconds=3.0,
                          output_name="tts.mp4", audio_wav=wav)
    assert mp4.exists()

    info = FFprobeValidator().probe(mp4)
    assert info.has_audio, "MP4 com narração deve ter áudio"

    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(mp4),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )
    output = proc.stdout + "\n" + proc.stderr
    mean_volume = None
    for line in output.splitlines():
        if "mean_volume:" in line:
            mean_volume = float(line.split("mean_volume:")[1].split("dB")[0].strip())
    assert mean_volume is not None and mean_volume > -60.0, \
        f"Áudio da narração não é audível (mean_volume={mean_volume} dB)"


# --------------------------------------------------------------------------- #
# P1-4: Falha de dependência externa gera erro explícito (não placeholder)    #
# --------------------------------------------------------------------------- #
def test_render_raises_when_chromium_missing(tmp_path, monkeypatch):
    """Sem Chromium/Node, a renderização deve levantar PipelineError (não silêncio)."""
    renderer = HTMLRenderer(output_dir=tmp_path / "renders", node_bin="node-nao-existe")
    ctx = {"video_id": "x", "template": "curiosity", "title": "T", "description": "D",
           "hashtags": [], "scenes": [], "duration_seconds": 1.0}
    with pytest.raises(PipelineError):
        renderer.render("curiosity", ctx, duration_seconds=1.0, output_name="nope.mp4")


# --------------------------------------------------------------------------- #
# P1-5: Caminhos independentes do diretório de trabalho (cwd)                 #
# --------------------------------------------------------------------------- #
def test_render_output_path_independent_of_cwd(tmp_path, monkeypatch):
    """O MP4 deve ser gerado no output_dir informado, independentemente do cwd."""
    out_dir = tmp_path / "renders"
    renderer = HTMLRenderer(output_dir=out_dir)
    ctx = {"video_id": "cwd", "template": "curiosity", "title": "T", "description": "D",
           "hashtags": [], "scenes": [], "duration_seconds": 1.0}
    monkeypatch.chdir(tempfile.mkdtemp())  # muda cwd para um diretório temporário
    mp4 = renderer.render("curiosity", ctx, duration_seconds=1.0, output_name="cwd.mp4")
    assert mp4.exists()
    assert str(mp4).startswith(str(out_dir))


# --------------------------------------------------------------------------- #
# P1-6: Integração RenderAgent → MP4 real no estado do workflow               #
# --------------------------------------------------------------------------- #
def test_render_agent_produces_real_mp4(tmp_path):
    """RenderAgent deve gerar video_assets com MP4 real e avançar para RENDERED."""
    mp4_src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", "color=c=red:s=1080x1920:r=30:d=2",
         "-f", "lavfi", "-i", "sine=frequency=440:d=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
         "-c:a", "aac", "-shortest", str(mp4_src)],
        capture_output=True, check=True,
    )

    state = _initial_state("vid-agent-real")
    state["storyboard"] = {"template": "curiosity", "total_seconds": 2.0}
    state["script"] = {
        "title": "T", "description": "D", "hashtags": ["#ia"],
        "scenes": [{"role": "gancho", "seconds": 2.0, "heading": "H",
                    "narration": ["Olá"], "source_ids": []}],
        "total_seconds": 2.0,
    }
    state["audio_assets"] = []

    out = RenderAgent()(state)
    assert out["video_assets"], "deve gerar video_assets"
    mp4_path = Path(out["video_assets"][0]["mp4_path"])
    assert mp4_path.exists(), f"MP4 deve existir: {mp4_path}"
    assert out["status"] == "RENDERED", out.get("status")
    assert out["current_stage"] == "RENDERED"

    info = FFprobeValidator().probe(mp4_path)
    assert info.width == 1080 and info.height == 1920
    assert abs(info.fps - 30.0) <= 1.0
    assert info.has_audio
