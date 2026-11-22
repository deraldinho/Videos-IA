"""Síntese de fala local com Piper TTS (pt-BR) — offline e gratuita.

Gera arquivos de áudio (WAV) a partir das frases de narração de cada cena do
roteiro. Se o Piper ou o modelo de voz não estiverem disponíveis, gera um
áudio de silêncio (fallback) para não bloquear o pipeline em DRY_RUN/REVIEW.
"""

from __future__ import annotations

import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from src.errors import PipelineError


@dataclass(frozen=True)
class AudioSegment:
    """Um segmento de áudio gerado pelo TTS."""
    scene_role: str
    index: int
    text: str
    wav_path: Path
    duration_seconds: float


class PiperTTS:
    """Interface para o Piper TTS local.

    Quando ``require_real`` é verdadeiro, a ausência do Piper ou do modelo de
    voz levanta :class:`PipelineError` em vez de gravar silêncio disfarçado.
    """

    def __init__(
        self,
        *,
        piper_bin: str = "piper",
        model_path: Path | str | None = None,
        output_dir: Path | str = "output/drafts",
        require_real: bool = False,
    ) -> None:
        self.piper_bin = piper_bin
        self.model_path = Path(model_path) if model_path else None
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.require_real = require_real

    def _synthesize(self, text: str, wav_path: Path) -> None:
        """Executa o Piper para gerar um WAV a partir de ``text``.

        Se ``require_real`` estiver ativo e o Piper/modelo não estiverem
        disponíveis, levanta :class:`PipelineError` (nunca grava silêncio como
        se fosse narração real).
        """
        if not self.model_path or not self.model_path.exists():
            if self.require_real:
                raise PipelineError(
                    f"Modelo de voz Piper não encontrado: {self.model_path}"
                )
            self._write_silence(wav_path, seconds=1.0)
            return

        cmd = [
            self.piper_bin,
            "--model", str(self.model_path),
            "--output_file", str(wav_path),
        ]
        try:
            proc = subprocess.run(
                cmd,
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=120,
            )
        except FileNotFoundError:
            if self.require_real:
                raise PipelineError(
                    f"Piper TTS não instalado (binário '{self.piper_bin}' não encontrado)."
                )
            # Piper não instalado: fallback de silêncio.
            self._write_silence(wav_path, seconds=1.0)
            return
        except subprocess.TimeoutExpired as exc:
            raise PipelineError(f"Piper excedeu o tempo limite para: {text!r}") from exc

        if proc.returncode != 0:
            raise PipelineError(
                f"Piper falhou (código {proc.returncode}): {proc.stderr.decode(errors='ignore')[:300]}"
            )

    @staticmethod
    def _write_silence(wav_path: Path, seconds: float = 1.0) -> None:
        """Escreve um WAV de silêncio (fallback quando o TTS não está disponível)."""
        sample_rate = 22050
        n_samples = int(sample_rate * seconds)
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * n_samples)

    @staticmethod
    def _wav_duration(wav_path: Path) -> float:
        with wave.open(str(wav_path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
        return frames / float(rate) if rate else 0.0

    def synthesize_scene(self, scene_role: str, phrases: Sequence[str],
                         index: int, prefix: str = "scene") -> list[AudioSegment]:
        """Gera um WAV por frase de narração da cena."""
        segments: list[AudioSegment] = []
        for i, phrase in enumerate(phrases):
            safe_role = "".join(c for c in scene_role if c.isalnum() or c == "-")
            wav_name = f"{prefix}_{index:02d}_{safe_role}_{i:02d}.wav"
            wav_path = self.output_dir / wav_name
            self._synthesize(phrase, wav_path)
            duration = self._wav_duration(wav_path)
            segments.append(AudioSegment(
                scene_role=scene_role,
                index=i,
                text=phrase,
                wav_path=wav_path,
                duration_seconds=duration,
            ))
        return segments

    def is_available(self) -> bool:
        """Retorna True se o Piper e o modelo de voz estiverem disponíveis."""
        if self.model_path is None or not self.model_path.exists():
            return False
        try:
            subprocess.run([self.piper_bin, "--help"], capture_output=True, timeout=10)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False


__all__ = ["PiperTTS", "AudioSegment"]
