"""Testes para os módulos de vídeo: TTS (fallback), renderer e validator (FFprobe)."""

from __future__ import annotations

from pathlib import Path
import tempfile
import wave

import pytest

from src.errors import PipelineError
from src.video.tts import PiperTTS
from src.video.validator import FFprobeValidator, VideoInfo


# --------------------------------------------------------------------------- #
# PiperTTS — fallback de silêncio quando o Piper/modelo não estão disponíveis #
# --------------------------------------------------------------------------- #
def test_tts_fallback_writes_silence_wav():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "audio"
        tts = PiperTTS(piper_bin="piper-nao-existe", model_path=None, output_dir=out)
        assert tts.is_available() is False
        segs = tts.synthesize_scene("gancho", ["Olá, mundo."], 0, prefix="test")
        assert len(segs) == 1
        wav = segs[0].wav_path
        assert wav.exists()
        # Verifica que é um WAV válido (silêncio).
        with wave.open(str(wav), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getframerate() == 22050
            assert wf.getnframes() > 0
        assert segs[0].duration_seconds > 0


def test_tts_is_available_false_without_model():
    with tempfile.TemporaryDirectory() as td:
        tts = PiperTTS(piper_bin="piper", model_path=Path(td) / "nao_existe.onnx")
        assert tts.is_available() is False


# --------------------------------------------------------------------------- #
# FFprobeValidator — erros de arquivo/ffprobe ausente                         #
# --------------------------------------------------------------------------- #
def test_validator_raises_when_file_missing():
    v = FFprobeValidator()
    with pytest.raises(PipelineError, match="não encontrado"):
        v.probe(Path("/caminho/inexistente.mp4"))


def test_validator_raises_when_ffprobe_missing(monkeypatch):
    v = FFprobeValidator(ffprobe_bin="ffprobe-nao-existe")
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fh:
        tmp = Path(fh.name)
    try:
        with pytest.raises(PipelineError, match="ffprobe não encontrado"):
            v.probe(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def test_validate_tiktok_rejects_missing_file():
    v = FFprobeValidator()
    with pytest.raises(PipelineError, match="não encontrado"):
        v.validate_tiktok(Path("/caminho/inexistente.mp4"))


__all__ = []
