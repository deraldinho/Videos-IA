"""Testes de regressão dos Quality Gates configuráveis em src/video/validator.py.

Cada teste é independente, usa ``tmp_path`` e gera os próprios MP4 com FFmpeg.
Cobrem os sete cenários definidos para os quality gates em ``strict=True``:

1. Vídeo preto + silencioso é rejeitado em strict=True.
2. Vídeo com frames pretos dentro do limite (max_black_ratio) não é rejeitado.
3. Áudio abaixo de min_mean_volume_db é rejeitado; áudio audível é aceito.
4. Vídeo estático (mesmo frame repetido) é rejeitado em strict=True.
5. Limites configuráveis funcionam (parâmetros max_black_ratio,
   max_continuous_black_seconds, min_mean_volume_db).
6. strict=False permanece permissivo (não rejeita por degeneração).
7. Vídeo real (não preto, com áudio) passa em strict=True.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.errors import PipelineError
from src.video.validator import FFprobeValidator

# --------------------------------------------------------------------------- #
# Helpers — geração de MP4 de teste com FFmpeg                                #
# --------------------------------------------------------------------------- #

RESOLUTION = "1080x1920"
FPS = 30


def _run_ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", *args], capture_output=True, check=True)


def make_black_silent(path: Path, seconds: float = 2.0) -> None:
    """Vídeo totalmente preto com áudio silêncio (anullsrc)."""
    _run_ffmpeg([
        "-f", "lavfi", "-i", f"color=c=black:s={RESOLUTION}:r={FPS}:d={seconds}",
        "-f", "lavfi", "-i", f"anullsrc=r=22050:d={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-shortest", str(path),
    ])


def make_real_dynamic(path: Path, seconds: float = 2.0) -> None:
    """Vídeo real: cor muda ao longo do tempo (não preto, não estático) com áudio audível."""
    _run_ffmpeg([
        "-f", "lavfi", "-i", f"color=c=red:s={RESOLUTION}:r={FPS}:d={seconds / 2}",
        "-f", "lavfi", "-i", f"color=c=blue:s={RESOLUTION}:r={FPS}:d={seconds / 2}",
        "-f", "lavfi", "-i", f"sine=frequency=440:d={seconds}",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
        "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-shortest", str(path),
    ])


def make_static(path: Path, seconds: float = 2.0) -> None:
    """Vídeo estático: mesmo frame (vermelho sólido) repetido, com áudio audível."""
    _run_ffmpeg([
        "-f", "lavfi", "-i", f"color=c=red:s={RESOLUTION}:r={FPS}:d={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:d={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-shortest", str(path),
    ])


def make_black_transition(path: Path) -> None:
    """Vídeo com segmento preto dentro do limite: 1s vermelho + 0.5s preto + 0.5s vermelho + áudio audível."""
    _run_ffmpeg([
        "-f", "lavfi", "-i", f"color=c=red:s={RESOLUTION}:r={FPS}:d=1",
        "-f", "lavfi", "-i", f"color=c=black:s={RESOLUTION}:r={FPS}:d=0.5",
        "-f", "lavfi", "-i", f"color=c=red:s={RESOLUTION}:r={FPS}:d=0.5",
        "-f", "lavfi", "-i", f"sine=frequency=440:d=2",
        "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p[v]",
        "-map", "[v]", "-map", "3:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-shortest", str(path),
    ])


def make_quiet_audio(path: Path, seconds: float = 2.0) -> None:
    """Vídeo vermelho com áudio abaixo do limite (mean_volume ≈ -81 dB < -60 dB)."""
    _run_ffmpeg([
        "-f", "lavfi", "-i", f"color=c=red:s={RESOLUTION}:r={FPS}:d={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:d={seconds},volume=0.001",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-shortest", str(path),
    ])


def make_quiet_dynamic_audio(path: Path, seconds: float = 2.0) -> None:
    """Vídeo com cenas dinâmicas + áudio abaixo do limite (mean_volume ≈ -81 dB < -60 dB).

    Combina cenas que mudam de cor com áudio silencioso para testar o quality gate
    de volume independente da detecção de cena estática.
    """
    _run_ffmpeg([
        "-f", "lavfi", "-i", f"color=c=red:s={RESOLUTION}:r={FPS}:d={seconds / 2}",
        "-f", "lavfi", "-i", f"color=c=blue:s={RESOLUTION}:r={FPS}:d={seconds / 2}",
        "-f", "lavfi", "-i", f"sine=frequency=440:d={seconds},volume=0.001",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
        "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-shortest", str(path),
    ])


def assert_rejected(path: Path, **kwargs) -> None:
    v = FFprobeValidator()
    with pytest.raises(PipelineError):
        v.validate_tiktok(path, strict=True, **kwargs)


def assert_accepted(path: Path, **kwargs) -> None:
    v = FFprobeValidator()
    info = v.validate_tiktok(path, strict=True, **kwargs)
    assert info is not None


# --------------------------------------------------------------------------- #
# 1. Vídeo preto + silencioso é rejeitado em strict=True                      #
# --------------------------------------------------------------------------- #
def test_quality_gate_rejeita_video_preto_silencioso_strict(tmp_path: Path) -> None:
    path = tmp_path / "black_silent.mp4"
    make_black_silent(path)
    assert_rejected(path)


# --------------------------------------------------------------------------- #
# 2. Frames pretos dentro do limite não são rejeitados                        #
# --------------------------------------------------------------------------- #
def test_quality_gate_nao_rejeita_preto_dentro_do_limite(tmp_path: Path) -> None:
    path = tmp_path / "black_transition.mp4"
    make_black_transition(path)
    # 0.5s de preto em 2s -> ratio 0.25 < 0.50 (default); 0.5s < 3.0s contínuo
    assert_accepted(path)


# --------------------------------------------------------------------------- #
# 3. Áudio abaixo do limite é rejeitado; áudio audível é aceito               #
# --------------------------------------------------------------------------- #
def test_quality_gate_rejeita_audio_abaixo_do_limite_strict(tmp_path: Path) -> None:
    path = tmp_path / "quiet_audio.mp4"
    make_quiet_audio(path)
    # mean_volume ≈ -81 dB < -60 dB (default) -> rejeitado como silêncio
    assert_rejected(path)


def test_quality_gate_aceita_audio_audavel(tmp_path: Path) -> None:
    path = tmp_path / "real_dynamic.mp4"
    make_real_dynamic(path)
    assert_accepted(path)


# --------------------------------------------------------------------------- #
# 4. Vídeo estático (mesmo frame repetido) é rejeitado em strict=True         #
# --------------------------------------------------------------------------- #
def test_quality_gate_rejeita_video_estatico_strict(tmp_path: Path) -> None:
    path = tmp_path / "static_red.mp4"
    make_static(path)
    assert_rejected(path)


# --------------------------------------------------------------------------- #
# 5. Limites configuráveis funcionam                                          #
# --------------------------------------------------------------------------- #
def test_quality_gate_limites_configuraveis(tmp_path: Path) -> None:
    # Apertar max_black_ratio faz o vídeo com transi��o preta ser rejeitado.
    transition = tmp_path / "black_transition.mp4"
    make_black_transition(transition)
    assert_rejected(transition, max_black_ratio=0.1)

    # Afrouxar min_mean_volume_db aceita o áudio baixo (mean ≈ -81 dB > -100 dB).
    # Usa vídeo com cenas dinâmicas para evitar falha no _assert_dynamic_scene.
    quiet = tmp_path / "quiet_dynamic_audio.mp4"
    make_quiet_dynamic_audio(quiet)
    assert_accepted(quiet, min_mean_volume_db=-100.0)


# --------------------------------------------------------------------------- #
# 6. strict=False permanece permissivo (não rejeita por degeneração)          #
# --------------------------------------------------------------------------- #
def test_quality_gate_strict_false_permissivo(tmp_path: Path) -> None:
    path = tmp_path / "black_silent.mp4"
    make_black_silent(path)
    v = FFprobeValidator()
    # Não deve levantar mesmo sendo preto/silencioso quando strict=False.
    info = v.validate_tiktok(path, strict=False)
    assert info is not None


# --------------------------------------------------------------------------- #
# 7. Vídeo real (não preto, com áudio) passa em strict=True                   #
# --------------------------------------------------------------------------- #
def test_quality_gate_video_real_passa_strict(tmp_path: Path) -> None:
    path = tmp_path / "real_dynamic.mp4"
    make_real_dynamic(path)
    assert_accepted(path)
