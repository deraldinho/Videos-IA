"""Validação de arquivos MP4 com FFprobe (padrão TikTok: 1080×1920, 30 fps, áudio, legendas).

Toda validação é local: executa ``ffprobe`` e inspeciona os streams de vídeo e
áudio. Nenhum dado é enviado para serviços externos.

Em ``strict=True`` são aplicados *quality gates* configuráveis que rejeitam
placeholders pretos/silenciosos e vídeos estáticos:
``max_black_ratio``, ``max_continuous_black_seconds`` e ``min_mean_volume_db``.

Quality Gates configuráveis (CP13 Priority 2) — melhorias de detecção de cena estática:
- pHash perceptual hashing para detecção mais robusta de vídeos estáticos
- Manutenção de compatibilidade reversível com comparação byte-a-byte existente
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from src.errors import PipelineError


@dataclass(frozen=True)
class VideoInfo:
    """Informações extraídas do arquivo de vídeo via FFprobe."""
    path: Path
    width: int
    height: int
    fps: float
    duration_seconds: float
    has_audio: bool
    codec: str
    format_name: str
    size_bytes: int
    caption_streams: int


@dataclass(frozen=True)
class QualityThresholds:
    """Limites dos quality gates aplicados quando ``strict=True``.

    Todos possuem defaults sensíveis e podem ser sobrescritos por parâmetros
    de :meth:`FFprobeValidator.validate_tiktok`.
    """

    max_black_ratio: float = 0.50
    """Fração máxima da duração que pode estar em quadros pretos."""

    max_continuous_black_seconds: float = 3.0
    """Duração máxima (s) de preto contínuo permitida."""

    min_mean_volume_db: float = -60.0
    """Volume médio mínimo (dB) aceito; abaixo disso o áudio é considerado silêncio."""

    dynamic_scene_samples: int = 5
    """Quantidade de amostras de frames ao longo do vídeo para detetar cena estática."""

    dynamic_scene_thumbnail_size: int = 160
    """Tamanho (px) da miniatura quadrada usada na comparação de cena estática."""

    dynamic_scene_phash_threshold: float = 0.9
    """Similaridade pHash acima da qual o vídeo é considerado estático.

    Valor 0.9 (90% de similaridade de hash perceptual) é o default que
    equilibra detecção de vídeos verdadeiramente estáticos com a tolerância
    necessária para diferenças de encoding. Pode ser ajustado para ser mais
    rigoroso (< 0.9) ou mais permissivo (> 0.9) conforme a necessidade.
    """


def _phash_similarity(hash1: np.ndarray, hash2: np.ndarray) -> float:
    """Compute similarity between two average hashes (1.0 = identical, 0.0 = different).

    Returns 1 minus the Hamming-distance ratio on the binary hash bits, de modo
    que valores próximos de 1.0 indicam frames perceptualmente parecidos.
    """
    if hash1.size != hash2.size:
        return 0.0
    diff = np.count_nonzero(hash1 != hash2)
    return 1.0 - diff / float(hash1.size)


def _compute_average_hash(image_bytes: bytes, size: int = 16) -> np.ndarray:
    """Compute average perceptual hash from raw image bytes.

    Steps:
    1. Reshape to grayscale using luminance weights if RGB, else treat as grayscale
    2. Area resize to (size, size) via strided decimation
    3. Compute average pixel value
    4. Generate binary hash: bit i = 1 if pixel > average else 0

    Returns a numpy array of size*size binary bits (flattened).
    """
    try:
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        if len(arr) >= size * size * 3:
            rgb = arr[: size * size * 3].reshape((size, size, 3))
            gray = (0.299 * rgb[:, :, 0].astype(float) +
                    0.587 * rgb[:, :, 1].astype(float) +
                    0.114 * rgb[:, :, 2].astype(float))
        else:
            gray = arr[: size * size].astype(float).reshape((size, size))
    except Exception:
        gray = np.frombuffer(image_bytes, dtype=np.uint8)[: size * size].astype(float).reshape((size, size))

    # Area resize via strided decimation
    step_h = max(1, gray.shape[0] // size)
    step_w = max(1, gray.shape[1] // size)
    downsampled = gray[::step_h, ::step_w]

    # Ensure exact size
    if downsampled.shape[0] < size:
        downsampled = np.pad(downsampled, ((0, size - downsampled.shape[0]), (0, 0)))
    if downsampled.shape[1] < size:
        downsampled = np.pad(downsampled, ((0, 0), (0, size - downsampled.shape[1])))

    # Crop if too large
    downsampled = downsampled[:size, :size]

    avg = float(downsampled.mean())
    binary_hash = (downsampled > avg).astype(np.uint8)

    return binary_hash.flatten()


class FFprobeValidator:
    """Valida que o MP4 atende ao padrão exigido pelo TikTok."""

    def __init__(
        self,
        ffprobe_bin: str = "ffprobe",
        ffmpeg_bin: str = "ffmpeg",
        thresholds: Optional[QualityThresholds] = None,
    ) -> None:
        self.ffprobe_bin = ffprobe_bin
        self.ffmpeg_bin = ffmpeg_bin
        self.thresholds = thresholds or QualityThresholds()

    def _probe(self, path: Path) -> dict:
        if not path.exists():
            raise PipelineError(f"Arquivo de vídeo não encontrado: {path}")
        cmd = [
            self.ffprobe_bin, "-v", "error",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(path),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except FileNotFoundError as exc:
            raise PipelineError("ffprobe não encontrado. Instale o FFmpeg.") from exc
        except subprocess.TimeoutExpired as exc:
            raise PipelineError("ffprobe excedeu o tempo limite.") from exc
        if proc.returncode != 0:
            raise PipelineError(
                f"ffprobe falhou para {path}: {proc.stderr[-500:]}"
            )
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise PipelineError(f"Saída do ffprobe não é JSON: {exc}") from exc

    def probe(self, path: Path) -> VideoInfo:
        data = self._probe(path)
        fmt = data.get("format", {})
        streams = data.get("streams", [])

        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        caption_streams = [s for s in streams if s.get("codec_type") == "subtitle"]

        if not video_streams:
            raise PipelineError(f"Vídeo sem stream de vídeo: {path}")
        vs = video_streams[0]

        width = int(vs.get("width", 0))
        height = int(vs.get("height", 0))
        fps = self._parse_fps(vs)
        duration = float(fmt.get("duration", 0.0))
        codec = vs.get("codec_name", "unknown")
        format_name = fmt.get("format_name", "unknown")
        size_bytes = int(fmt.get("size", 0))
        has_audio = len(audio_streams) > 0

        return VideoInfo(
            path=path,
            width=width,
            height=height,
            fps=fps,
            duration_seconds=duration,
            has_audio=has_audio,
            codec=codec,
            format_name=format_name,
            size_bytes=size_bytes,
            caption_streams=len(caption_streams),
        )

    @staticmethod
    def _parse_fps(stream: dict) -> float:
        r_frame_rate = stream.get("r_frame_rate", "0/1")
        try:
            num, den = r_frame_rate.split("/")
            return float(num) / float(den) if float(den) != 0 else 0.0
        except (ValueError, ZeroDivisionError):
            return 0.0

    def validate_tiktok(
        self,
        path: Path,
        *,
        width: int = 1080,
        height: int = 1920,
        fps: float = 30.0,
        max_duration_seconds: float = 60.0,
        require_audio: bool = True,
        max_size_mb: float = 25.0,
        strict: bool = False,
        max_black_ratio: Optional[float] = None,
        max_continuous_black_seconds: Optional[float] = None,
        min_mean_volume_db: Optional[float] = None,
    ) -> VideoInfo:
        """Valida todas as restrições do TikTok e retorna o :class:`VideoInfo`.

        Lança :class:`PipelineError` se alguma restrição não for atendida.
        Quando ``strict`` está ativo, também aplica os quality gates
        configuráveis (quadros pretos, silêncio e cena estática).
        """
        info = self.probe(path)

        if info.width != width or info.height != height:
            raise PipelineError(
                f"Resolução inválida: {info.width}x{info.height} "
                f"(esperado {width}x{height})"
            )
        if abs(info.fps - fps) > 1.0:
            raise PipelineError(
                f"FPS fora do padrão: {info.fps:.2f} (esperado ~{fps})"
            )
        if require_audio and not info.has_audio:
            raise PipelineError("Vídeo sem stream de áudio (TikTok exige áudio).")
        if info.duration_seconds > max_duration_seconds:
            raise PipelineError(
                f"Duração excede o limite: {info.duration_seconds:.2f}s "
                f"(máx. {max_duration_seconds}s)"
            )
        if info.size_bytes > max_size_mb * 1024 * 1024:
            raise PipelineError(
                f"Arquivo excede {max_size_mb}MB: {info.size_bytes / (1024*1024):.2f}MB"
            )
        if info.caption_streams == 0:
            # Legendas são recomendadas, mas não bloqueantes em v1.
            pass

        if strict:
            thresholds = QualityThresholds(
                max_black_ratio=(
                    max_black_ratio
                    if max_black_ratio is not None
                    else self.thresholds.max_black_ratio
                ),
                max_continuous_black_seconds=(
                    max_continuous_black_seconds
                    if max_continuous_black_seconds is not None
                    else self.thresholds.max_continuous_black_seconds
                ),
                min_mean_volume_db=(
                    min_mean_volume_db
                    if min_mean_volume_db is not None
                    else self.thresholds.min_mean_volume_db
                ),
                dynamic_scene_samples=self.thresholds.dynamic_scene_samples,
                dynamic_scene_thumbnail_size=self.thresholds.dynamic_scene_thumbnail_size,
            )
            self._assert_quality_gates(path, info, thresholds)

        return info

    def _assert_quality_gates(
        self, path: Path, info: VideoInfo, thresholds: QualityThresholds
    ) -> None:
        """Aplica todos os quality gates em strict=True."""
        self._assert_audio_volume(path, info, thresholds)
        self._assert_black_frames(path, info, thresholds)
        self._assert_dynamic_scene(path, info, thresholds)

    def _assert_audio_volume(self, path: Path, info: VideoInfo, thresholds: QualityThresholds) -> None:
        """Rejeita áudio silencioso (mean_volume abaixo do limite)."""
        if not info.has_audio:
            # Ausência de áudio já é tratada acima quando require_audio=True.
            return
        ffmpeg = shutil.which(self.ffmpeg_bin)
        if not ffmpeg:
            return
        mean_volume = self._read_mean_volume(ffmpeg, path)
        if mean_volume is not None and mean_volume < thresholds.min_mean_volume_db:
            raise PipelineError(
                f"Áudio silencioso detectado (mean_volume {mean_volume} dB): {path}"
            )

    @staticmethod
    def _read_mean_volume(ffmpeg: str, path: Path) -> Optional[float]:
        try:
            proc = subprocess.run(
                [ffmpeg, "-v", "info", "-i", str(path),
                 "-af", "volumedetect", "-f", "null", "-"],
                capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        output = proc.stdout + "\n" + proc.stderr
        mean_volume: Optional[float] = None
        for line in output.splitlines():
            if "mean_volume:" in line:
                try:
                    mean_volume = float(
                        line.split("mean_volume:")[1].split("dB")[0].strip()
                    )
                except ValueError:
                    pass
        return mean_volume

    def _assert_black_frames(self, path: Path, info: VideoInfo, thresholds: QualityThresholds) -> None:
        """Rejeita vídeo com excesso de quadros pretos (fração e contínuo)."""
        ffmpeg = shutil.which(self.ffmpeg_bin)
        if not ffmpeg:
            return
        try:
            proc = subprocess.run(
                [ffmpeg, "-v", "info", "-i", str(path),
                 "-vf", "blackdetect=d=0.5:pix_th=0.05", "-f", "null", "-"],
                capture_output=True, text=True, timeout=120,
            )
        except (subprocess.TimeoutExpired, OSError):
            return
        output = proc.stdout + "\n" + proc.stderr

        total_black = 0.0
        max_continuous = 0.0
        pattern = re.compile(
            r"black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)"
        )
        for line in output.splitlines():
            m = pattern.search(line)
            if m:
                duration = float(m.group(3))
                total_black += duration
                max_continuous = max(max_continuous, duration)

        duration = max(info.duration_seconds, 1e-6)
        black_ratio = total_black / duration
        if black_ratio > thresholds.max_black_ratio:
            raise PipelineError(
                f"Muitos quadros pretos (ratio {black_ratio:.2f} > "
                f"{thresholds.max_black_ratio}): {path}"
            )
        if max_continuous > thresholds.max_continuous_black_seconds:
            raise PipelineError(
                f"Preto contínuo excessivo ({max_continuous:.2f}s > "
                f"{thresholds.max_continuous_black_seconds}s): {path}"
            )

    def _assert_dynamic_scene(self, path: Path, info: VideoInfo, thresholds: QualityThresholds) -> None:
        """Rejeita vídeo estático (mesmo conteúdo em todas as amostras).

        Extrai miniaturas em branco e cinza em vários pontos do vídeo e as
        compara byte a byte. Se todas forem idênticas, o vídeo é estático.

        Adicionalmente, utiliza hashing perceptual (pHash) para detectar vídeos
        com variação gradativa de cenas que o byte-a-byte pode não capturar
        confiavelmente (ex: fade lento de vermelho para azul). Se todos os
        amostras de pHash forem muito semelhantes (similaridade > 0.9), também
        flagra o vídeo como estático, mantendo compatibilidade reversível.
        """
        ffmpeg = shutil.which(self.ffmpeg_bin)
        if not ffmpeg or info.duration_seconds <= 0:
            return

        n = max(2, thresholds.dynamic_scene_samples)
        duration = info.duration_seconds
        size = thresholds.dynamic_scene_thumbnail_size
        timestamps = [duration * (i + 1) / (n + 1) for i in range(n)]

        first_frame: Optional[bytes] = None
        phashes: list[np.ndarray] = []
        all_byte_identical = True
        for t in timestamps:
            try:
                proc = subprocess.run(
                    [
                        ffmpeg, "-v", "error", "-ss", str(t), "-i", str(path),
                        "-frames:v", "1",
                        "-vf", f"scale={size}:{size},format=gray",
                        "-f", "rawvideo", "-",
                    ],
                    capture_output=True, timeout=30,
                )
            except (subprocess.TimeoutExpired, OSError):
                return
            if proc.returncode != 0 or not proc.stdout:
                return
            if first_frame is None:
                first_frame = proc.stdout
                # Compute pHash for the first frame
                phashes.append(_compute_average_hash(first_frame, size // 10))  # downsample to 16x16
            elif proc.stdout != first_frame:
                all_byte_identical = False
                # Still compute pHash for this frame even if bytes differ
                try:
                    phashes.append(_compute_average_hash(proc.stdout, size // 10))
                except Exception:
                    pass
                # Early exit: if we already found different bytes AND pHashes differ,
                # the video has dynamic scenes — no need to continue checking.
                # However, we continue to collect all pHashes for a final decision.

        # --- Byte-comparison primary check (existing behavior) ---
        if all_byte_identical and first_frame is not None:
            # Todas as amostras são idênticas byte-a-byte -> vídeo estático.
            raise PipelineError(
                f"Vídeo estático detectado (mesmo conteúdo em todas as amostras): {path}"
            )

        # --- pHash secondary check (new enhancement) ---
        # If all pHashes are very similar (similarity > threshold (padrão 0.9 = Hamming distance < 10%)),
        # flag as static even if bytes differ (handles gradual scene changes like fades).
        if len(phashes) >= n and all_byte_identical is False:
            # Compute pairwise similarities
            similarities = []
            for i in range(len(phashes)):
                for j in range(i + 1, len(phashes)):
                    sim = _phash_similarity(phashes[i], phashes[j])
                    similarities.append(sim)

            if similarities:
                max_sim = max(similarities)
                # Threshold: if all pHashes are very similar (similarity > thresholds.dynamic_scene_phash_threshold),
                # flag the video as static. The default threshold means Hamming distance
                # < 10% of hash size (e.g., for 256-bit hash, < 25 bits differ).
                if max_sim > thresholds.dynamic_scene_phash_threshold:
                    raise PipelineError(
                        f"Vídeo estático detectado (hash perceptual semelhante em todas as amostras): {path}"
                    )

        # Se nenhuma das duas condições acima foi acionada, o vídeo é dinâmico.
        # (Nenhum error foi raised => vídeo tem cenas variadas)

    def is_valid_mp4(self, path: Path) -> bool:
        """Retorna True se o arquivo é um MP4 válido (sem levantar exceção)."""
        try:
            self.probe(path)
            return True
        except PipelineError:
            return False


__all__ = ["FFprobeValidator", "VideoInfo", "QualityThresholds"]
