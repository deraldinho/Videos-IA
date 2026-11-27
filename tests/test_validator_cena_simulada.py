"""Regressões da análise visual sem chamar FFmpeg nem renderizar mídia."""

import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.errors import PipelineError
from src.video.validator import FFprobeValidator, QualityThresholds, VideoInfo


MEDIA = Path("simulado.mp4")
INFO = VideoInfo(MEDIA, 1080, 1920, 30, 31, True, "h264", "mp4", 256, 0)


def simular_quadros(monkeypatch, *quadros):
    amostras = iter(quadros)
    monkeypatch.setattr("src.video.validator.shutil.which", lambda _: "ffmpeg")
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs:
                        subprocess.CompletedProcess(args, 0, next(amostras).tobytes(), b""))


def padrao():
    frame = np.full((160, 160), 20, dtype=np.uint8)
    frame[80:, :80] = 180
    return frame


def test_analisa_miniatura_inteira_e_nao_so_margem_superior(monkeypatch):
    esquerda = padrao()
    direita = np.fliplr(esquerda)
    simular_quadros(monkeypatch, esquerda, direita)
    FFprobeValidator()._assert_dynamic_scene(MEDIA, INFO, QualityThresholds(dynamic_scene_samples=2))


def test_duas_amostras_parecidas_nao_anulam_terceira_diferente(monkeypatch):
    esquerda = padrao()
    simular_quadros(monkeypatch, esquerda, esquerda + 1, np.fliplr(esquerda))
    FFprobeValidator()._assert_dynamic_scene(MEDIA, INFO, QualityThresholds(dynamic_scene_samples=3))


def test_rejeita_cena_estatica_com_repeticao_e_ruido(monkeypatch):
    frame = padrao()
    simular_quadros(monkeypatch, frame, frame, frame + 1)
    with pytest.raises(PipelineError, match="estático"):
        FFprobeValidator()._assert_dynamic_scene(MEDIA, INFO, QualityThresholds(dynamic_scene_samples=3))


def test_transicao_de_luminancia_uniforme_nao_e_estatica(monkeypatch):
    claro = np.full((160, 160), 80, dtype=np.uint8)
    escuro = np.full((160, 160), 30, dtype=np.uint8)
    simular_quadros(monkeypatch, claro, escuro)
    FFprobeValidator()._assert_dynamic_scene(MEDIA, INFO, QualityThresholds(dynamic_scene_samples=2))


def test_preserva_limiar_configurado_no_gate_estrito(monkeypatch):
    validador = FFprobeValidator(thresholds=QualityThresholds(dynamic_scene_phash_threshold=0.97))
    monkeypatch.setattr(validador, "probe", lambda _: INFO)
    recebidos = []
    monkeypatch.setattr(validador, "_assert_quality_gates", lambda path, info, limiares: recebidos.append(limiares))
    validador.validate_tiktok(MEDIA, strict=True)
    assert recebidos[0].dynamic_scene_phash_threshold == 0.97
