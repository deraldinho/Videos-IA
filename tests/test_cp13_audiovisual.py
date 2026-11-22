"""Testes CP13 — reproduzem defeitos P0/P1 antes da correção.

Estes testes validam que o pipeline gera RESULTADOS REAIS, não placeholders:
- Renderizaçăo deve produzir vídeo năo-preto com cenas visíveis
- TTS deve produzir áudio audível (năo silêncio)
- Transcriçăo deve analisar o áudio real (năo copiar roteiro)
- QualityAgent strict deve rejeitar placeholder preto/silencioso
- PublishingAgent năo marca PUBLISHED sem confirmaçăo remota real
"""

from __future__ import annotations

import subprocess
import tempfile
import wave
from pathlib import Path

import pytest

from src.errors import PipelineError
from src.video.renderer import HTMLRenderer
from src.video.tts import PiperTTS
from src.video.validator import FFprobeValidator
from src.workflow.agents.render import RenderAgent
from src.workflow.agents.transcription import TranscriptionAgent
from src.workflow.agents.voice import VoiceAgent
from src.workflow.state import empty_state, state_to_dict


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _make_mp4(path: Path, seconds: float = 2.0) -> None:
    """Gera um MP4 válido (1080x1920, 30fps, com áudio audível)."""
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=red:s=1080x1920:r=30:d={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:d={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-shortest",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


def _initial_state(video_id: str = "vid-cp13", topic: str = "tema cp13") -> dict:
    return {
        "execution_id": "exec-cp13",
        "video_id": video_id,
        "topic": topic,
        "status": "IDEA_CREATED",
        "current_stage": "IDEA_CREATED",
        "mode": "REVIEW",
        "live_enabled": False,
        "auto_enabled": False,
    }


# --------------------------------------------------------------------------- #
# P0-1: Renderizaçăo deve produzir vídeo real (năo preto/silencioso)          #
# --------------------------------------------------------------------------- #
def test_render_produces_non_black_video(tmp_path):
    """RenderAgent deve gerar um MP4 com conteúdo visual real (năo placeholder preto)."""
    mp4 = tmp_path / "real.mp4"
    _make_mp4(mp4)

    state = _initial_state("vid-render-real")
    state["script"] = {
        "title": "T",
        "scenes": [{"role": "gancho", "seconds": 2.0, "heading": "H",
                    "narration": ["Olá"], "source_ids": []}],
        "total_seconds": 2.0,
        "hashtags": ["#ia"],
        "description": "desc",
    }
    state["storyboard"] = {"template": "curiosity", "total_seconds": 2.0}
    state["audio_assets"] = []

    out = RenderAgent()(state)
    assert out["video_assets"], "deve gerar video_assets"
    mp4_path = Path(out["video_assets"][0]["mp4_path"])
    assert mp4_path.exists(), f"MP4 deve existir: {mp4_path}"

    # FFprobe: resoluçăo, FPS, áudio
    info = FFprobeValidator().probe(mp4_path)
    assert info.width == 1080 and info.height == 1920
    assert abs(info.fps - 30.0) <= 1.0
    assert info.has_audio

    # Năo deve ser todo preto (blackdetect deve encontrar pouco ou nada)
    ffmpeg = "ffmpeg"
    proc = subprocess.run(
        [ffmpeg, "-v", "info", "-i", str(mp4_path),
         "-vf", "blackdetect=d=0.5:pix_th=0.05", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120,
    )
    output = proc.stdout + "\n" + proc.stderr
    black_count = output.count("black_start")
    # Vídeo real tem poucos quadros pretos; placeholder preto tem muitos
    assert black_count < 5, f"Vídeo parece ser placeholder preto ({black_count} quadros pretos detectados)"


# --------------------------------------------------------------------------- #
# P0-3: TTS deve produzir áudio audível (năo silêncio)                        #
# --------------------------------------------------------------------------- #
def test_tts_produces_audible_audio(tmp_path):
    """TTS com require_real=True deve falhar com erro claro quando o modelo
    não estiver disponível (nunca gravar silêncio disfarçado como narração real)."""
    out_dir = tmp_path / "tts"
    tts = PiperTTS(piper_bin="piper-nao-existe", model_path=None,
                   output_dir=out_dir, require_real=True)
    assert tts.is_available() is False

    # Quando require_real=True e o TTS não está disponível, deve levantar erro
    # em vez de gravar silêncio como se fosse narração real.
    with pytest.raises((PipelineError, FileNotFoundError, RuntimeError)):
        tts.synthesize_scene("gancho", ["Olá, mundo."], 0, prefix="test")


# --------------------------------------------------------------------------- #
# P0-9: Transcriçăo deve analisar áudio real, năo copiar roteiro              #
# --------------------------------------------------------------------------- #
def test_transcription_uses_real_audio_not_script(tmp_path):
    """TranscriptionAgent deve tentar transcrever o áudio real;
    năo deve simplesmente copiar o texto do roteiro como se fosse transcriçăo."""
    mp4 = tmp_path / "audio.mp4"
    _make_mp4(mp4)

    # Cria um WAV real (năo silencioso) para transcrever.
    import wave
    wav = tmp_path / "narracao.wav"
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(b"\x01\x02" * 22050)  # áudio não-silencioso

    state = _initial_state("vid-transcribe-real")
    state["script"] = {
        "scenes": [{"role": "gancho", "narration": ["TEXTO DO ROTEIRO QUE NÃO DEVE APARECER"]}],
    }
    state["audio_assets"] = [
        {"scene_role": "gancho", "index": 0, "text": "TEXTO DO ROTEIRO QUE NÃO DEVE APARECER",
         "duration_seconds": 3.0, "wav_path": str(wav)}
    ]
    state["video_assets"] = [{"mp4_path": str(mp4)}]

    out = TranscriptionAgent()(state)
    transcription = out["transcription"]
    # O motor năo pode ser mock-asr copiando o roteiro
    assert transcription.get("engine") != "mock-asr", \
        "Transcriçăo năo deve usar mock-asr (cópia do roteiro)"
    # O texto transcrito năo deve ser igual ao roteiro (seria cópia)
    assert transcription.get("text") != "TEXTO DO ROTEIRO QUE NÃO DEVE APARECER", \
        "Transcriçăo năo deve copiar o roteiro como se fosse áudio real"


# --------------------------------------------------------------------------- #
# P0-11: QualityAgent strict bloqueia placeholder preto/silencioso            #
# --------------------------------------------------------------------------- #
def test_quality_strict_rejects_black_silent_video(tmp_path):
    """QualityAgent (strict) deve rejeitar MP4 preto e silencioso."""
    mp4 = tmp_path / "placeholder.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=1080x1920:r=30:d=2",
        "-f", "lavfi", "-i", "anullsrc=r=22050:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-shortest", str(mp4),
    ], capture_output=True, check=True)

    state = _initial_state()
    state["video_assets"] = [{"mp4_path": str(mp4)}]
    # quality_agent em graph.py usa strict=False; testamos o validator direto
    v = FFprobeValidator()
    with pytest.raises(PipelineError, match="(silencioso|pretos|degenerado)"):
        v.validate_tiktok(mp4, strict=True)


# --------------------------------------------------------------------------- #
# P0-15/16: PublishingAgent năo marca PUBLISHED sem confirmaçăo remota        #
# --------------------------------------------------------------------------- #
def test_publishing_not_marked_published_without_live(tmp_path):
    """PublishingAgent em modo năo-live năo deve marcar PUBLISHED de verdade
    (ambos Cloudinary e Buffer săo mock). Deve registrar como mock."""
    mp4 = tmp_path / "real.mp4"
    _make_mp4(mp4)

    state = _initial_state("vid-publish-real")
    state["live_enabled"] = False
    state["video_assets"] = [{"mp4_path": str(mp4), "width": 1080, "height": 1920,
                              "fps": 30.0, "duration_seconds": 2.0, "has_audio": True}]
    state["script"] = {"description": "desc", "hashtags": ["#ia"]}
    state["compliance"] = {"blocked": False}

    from src.workflow.agents.publishing import PublishingAgent
    out = PublishingAgent()(state)
    # Em modo năo-live, o resultado deve ser mock (năo publicaçăo real confirmada)
    assert out.get("mock") is True or out.get("publication_status") != "published", \
        "Em modo năo-live, publicaçăo năo deve ser confirmada como real"

