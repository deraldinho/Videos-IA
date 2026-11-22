"""Adaptador de ASR (reconhecimento automático de fala).

Tenta usar um motor de ASR real (Parakeet/Whisper) quando disponível.
Quando nenhum motor real está instalado, faz uma análise de áudio básica com
FFmpeg para confirmar que há áudio audível e extrair duração — nunca copia o
roteiro como se fosse uma transcrição.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.errors import PipelineError


@dataclass(frozen=True)
class ASRResult:
    """Resultado de uma transcrição de áudio."""
    text: str
    confidence: float
    engine: str
    segments: list[dict]
    duration_seconds: float
    has_audio: bool


class ASRAdapter:
    """Adaptador de ASR com fallback para análise de áudio real."""

    def __init__(self, ffmpeg_bin: str = "ffmpeg") -> None:
        self.ffmpeg_bin = ffmpeg_bin

    def transcribe(self, audio_path: Path) -> ASRResult:
        """Transcreve o arquivo de áudio.

        Tenta motores de ASR reais em ordem de preferência. Se nenhum estiver
        disponível, recorre à análise de áudio com FFmpeg para confirmar
        que há conteúdo audível e reportar duração — sem inventar texto.
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise PipelineError(f"Arquivo de áudio não encontrado: {audio_path}")

        # --- Tenta motor de ASR real --------------------------------------- #
        result = self._try_real_asr(audio_path)
        if result is not None:
            return result

        # --- Fallback: análise de áudio real com FFmpeg -------------------- #
        return self._analyze_audio(audio_path)

    # ------------------------------------------------------------------ #
    # Motores de ASR reais                                                 #
    # ------------------------------------------------------------------ #
    def _try_real_asr(self, audio_path: Path) -> Optional[ASRResult]:
        """Tenta usar um motor de ASR real instalado no sistema."""
        # Parakeet (NVIDIA) — suporte a PT-BR
        try:
            import nemo.collections.asr as nemo_asr  # type: ignore
            model = nemo_asr.models.ASRModel.from_pretrained(
                model_name="nvidia/parakeet-tdt-0.6b-v2"
            )
            transcription = model.transcribe([str(audio_path)])[0]
            text = getattr(transcription, "text", "") or ""
            return ASRResult(
                text=text,
                confidence=0.9,
                engine="parakeet",
                segments=[{"text": text, "start": 0.0,
                           "end": self._wav_duration(audio_path)}],
                duration_seconds=self._wav_duration(audio_path),
                has_audio=len(text) > 0,
            )
        except Exception:
            pass

        # Whisper (OpenAI) como alternativa
        try:
            import whisper  # type: ignore
            model = whisper.load_model("base")
            result = model.transcribe(str(audio_path))
            text = result.get("text", "") or ""
            segments = [
                {"text": s.get("text", ""), "start": s.get("start", 0.0),
                 "end": s.get("end", 0.0)}
                for s in result.get("segments", [])
            ]
            return ASRResult(
                text=text,
                confidence=0.85,
                engine="whisper",
                segments=segments,
                duration_seconds=self._wav_duration(audio_path),
                has_audio=len(text) > 0,
            )
        except Exception:
            pass

        return None

    # ------------------------------------------------------------------ #
    # Análise de áudio real (fallback sem motor de ASR)                   #
    # ------------------------------------------------------------------ #
    def _analyze_audio(self, audio_path: Path) -> ASRResult:
        """Analisa o áudio com FFmpeg para confirmar conteúdo audível.

        Não gera texto transcrito (não há motor ASR). Retorna um marcador
        claro indicando que a transcrição real não pôde ser feita, mas o
        áudio foi verificado como presente.
        """
        duration = self._wav_duration(audio_path)
        mean_volume = self._measure_volume(audio_path)
        has_audio = mean_volume is not None and mean_volume > -60.0

        if not has_audio:
            raise PipelineError(
                f"Áudio não audível detectado (mean_volume={mean_volume} dB): {audio_path}"
            )

        return ASRResult(
            text="",  # Nenhum texto inventado
            confidence=0.0,
            engine="audio-analysis",
            segments=[],
            duration_seconds=duration,
            has_audio=True,
        )

    @staticmethod
    def _wav_duration(wav_path: Path) -> float:
        """Retorna a duração de um WAV em segundos."""
        import wave
        with wave.open(str(wav_path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
        return frames / float(rate) if rate else 0.0

    def _measure_volume(self, audio_path: Path) -> Optional[float]:
        """Mede o volume médio do áudio com ffmpeg (volumedetect)."""
        try:
            proc = subprocess.run(
                [self.ffmpeg_bin, "-v", "info", "-i", str(audio_path),
                 "-af", "volumedetect", "-f", "null", "-"],
                capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        output = proc.stdout + "\n" + proc.stderr
        for line in output.splitlines():
            if "mean_volume:" in line:
                try:
                    return float(
                        line.split("mean_volume:")[1].split("dB")[0].strip()
                    )
                except ValueError:
                    pass
        return None


__all__ = ["ASRAdapter", "ASRResult"]
