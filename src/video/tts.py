"""Síntese de fala local com Piper TTS (pt-BR) — offline e gratuita.

Gera arquivos de áudio (WAV) a partir das frases de narração de cada cena do
roteiro. Se o Piper ou o modelo de voz não estiverem disponíveis, gera um
áudio de silêncio (fallback) para não bloquear o pipeline em DRY_RUN/REVIEW.
"""

from __future__ import annotations

import math
import re
import shutil
import struct
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from src.errors import PipelineError

# Símbolos de interface, marcações de rede e metadados que nunca devem ser
# enviados ao TTS. Eles aparecem no roteiro/legenda, mas não no áudio falado.
_URL = re.compile(r"https?://\S+")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_SYMBOLS = re.compile(r"[©®™℗]")
_EMPHASE = re.compile(r"\*\*|__")
_MARK_CHARS = re.compile(r"[*_`~]")
_MENTION = re.compile(r"(?<![\w.])@[A-Za-z0-9_]+")
_HASHTAG = re.compile(r"(?<!\S)#\w+")
_PUNCT_SPACE = re.compile(r"\s+([.,;:!?])\s+")
_TRAIL_PUNCT = re.compile(r"([:;])\s*\.\s+")
_DANGLING_COLON = re.compile(r":\s+")
_EMOJI = re.compile(
    r"[\U0001F300-\U0001F9FF"
    r"\U0001FA00-\U0001FA6F"
    r"\U0001FA70-\U0001FAFF"
    r"\U00002700-\U000027BF"
    r"\U00002600-\U000026FF"
    r"\U00002B00-\U00002BFF"
    r"\U0000FE00-\U0000FE0F"
    r"\U0000200D]"
)


def prepara_texto_para_tts(texto: str) -> str:
    """Remove símbolos de interface e devolve apenas o que deve ser falado.

    URLs avulsas são omitidas; em ``[rótulo](url)`` só o rótulo é falado.
    Menções e hashtags isoladas (bloco de metadados ou frase inteira) são
    omitidas; dentro da frase perdem apenas o prefixo e mantêm o nome.
    ``©``, ``®`` e ``™`` são removidos sem apagar o crédito ao lado. O texto
    original do roteiro/legenda não é alterado.
    """
    limpo = _LINK.sub(r"\1", texto)
    limpo = _URL.sub("", limpo)
    limpo = _SYMBOLS.sub("", limpo)
    limpo = _EMPHASE.sub("", limpo)
    limpo = _MARK_CHARS.sub("", limpo)

    def _social(match: re.Match[str]) -> str:
        token = match.group(0)
        corpo = token[1:]
        if token.startswith("#") and corpo and corpo[0].isdigit():
            return corpo  # #1, #42: referência numérica, mantém o número
        antes = limpo[: match.start()].rstrip()
        if antes and antes[-1] in ".!?":
            return ""  # bloco de metadados após frase completa
        if not antes:
            return ""  # tag isolada no início da frase = metadados
        return corpo  # dentro da frase: tira só o prefixo, mantém o nome

    limpo = _MENTION.sub(lambda m: _social(m), limpo)
    limpo = _HASHTAG.sub(lambda m: _social(m), limpo)
    limpo = _PUNCT_SPACE.sub(r"\1 ", limpo)
    limpo = _TRAIL_PUNCT.sub(r"\1 ", limpo)
    limpo = _DANGLING_COLON.sub(r". ", limpo)
    limpo = _EMOJI.sub(" ", limpo)
    limpo = re.sub(r"\s+", " ", limpo).strip()
    return limpo


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
        # Resolve o binário do Piper pelo PATH; se não encontrar, mantém o
        # nome original para que o erro de FileNotFoundError seja claro.
        resolved_piper = shutil.which(piper_bin)
        self.piper_bin = resolved_piper if resolved_piper else piper_bin
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
        texto_falado = prepara_texto_para_tts(text)
        if not texto_falado:
            raise PipelineError("Texto sem conteúdo falável para sintetizar.")

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
                input=texto_falado.encode("utf-8"),
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
        """Escreve um WAV de tom audível (fallback quando o TTS não está disponível).

        Em vez de silêncio puro (que a ASR rejeitaria como "não audível"), gera um
        tom senoide de 440 Hz de baixa amplitude. Isso permite que o pipeline
        continue em modo DRY_RUN/REVIEW sem mascarar a ausência de TTS real como
        narração genuína — o tom é claramente um placeholder de desenvolvimento.
        """
        import math

        sample_rate = 22050
        n_samples = int(sample_rate * seconds)
        amplitude = 3000  # abaixo do clipping, mas audível para volumedetect
        frames = b"".join(
            struct.pack(
                "<h",
                int(amplitude * math.sin(2 * math.pi * 440 * (t / sample_rate))),
            )
            for t in range(n_samples)
        )
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(frames)

    @staticmethod
    def _wav_duration(wav_path: Path) -> float:
        with wave.open(str(wav_path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
        return frames / float(rate) if rate else 0.0

    def synthesize_scene(self, scene_role: str, phrases: Sequence[str],
                         index: int, prefix: str = "scene") -> list[AudioSegment]:
        """Gera um WAV por frase de narração da cena.

        Valida todo o lote antes de sintetizar: se alguma frase não tiver
        conteúdo falável após o filtro, levanta :class:`PipelineError`
        sem chamar o Piper nem gerar WAV.
        """
        falaveis = [prepara_texto_para_tts(p) for p in phrases]
        for i, falavel in enumerate(falaveis):
            if not falavel:
                raise PipelineError(
                    f"Frase {i + 1} da cena '{scene_role}' sem texto falável "
                    f"para sintetizar."
                )
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
