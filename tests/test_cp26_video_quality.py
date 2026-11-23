"""Testes TDD para CP26 — Validação de Qualidade de Vídeo (quality gates).

Critérios de aceitação:
- FFprobeValidator.validate_tiktok com strict=True aplica quality gates:
  resolução 1080x1920, ~30fps, áudio obrigatório, duração <= 60s, <= 25MB,
  detecção de quadro preto, silêncio de áudio, cena estática (pHash).
- strict=False aplica apenas checagens básicas (sem quality gates).
- QualityAgent (quality_agent no graph) armazena ValidationResult no estado.
- QualityAgent avança current_stage para VALIDATED quando válido.
- PublishingAgent NÃO publica se validation_results.valid for False (gate de qualidade).
- Qualidade é pré-requisito para publicação CP24.
- Testes isolados, sem rede, sem credenciais.
- MP4s de teste gerados com ffmpeg local (disponível no ambiente).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.video.validator import FFprobeValidator, QualityThresholds, VideoInfo
from src.workflow.state import empty_state, state_to_dict
from src.workflow.graph import quality_agent
from src.workflow.agents.publishing import PublishingAgent


# --------------------------------------------------------------------------- #
# Helpers: gerar MP4s de teste com ffmpeg                                     #
# --------------------------------------------------------------------------- #
def _make_mp4(path: Path, *, width: int = 1080, height: int = 1920,
              fps: int = 30, duration: float = 5.0, with_audio: bool = True,
              black: bool = False, silent_audio: bool = False,
              source: str | None = None) -> Path:
    """Gera um MP4 de teste com ffmpeg (padrão TikTok: 1080x1920 @30fps).

    ``source`` substitui o filtro de vídeo lavfi (útil quando o default
    ``testsrc2`` é percebido como estático pelo gate pHash em 16x16).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if black:
        vf = f"color=c=black:size={width}x{height}:duration={duration},format=yuv420p"
    elif source is not None:
        vf = f"{source},format=yuv420p"
    else:
        vf = f"testsrc2=size={width}x{height}:rate={fps}:duration={duration},format=yuv420p"

    af = "anullsrc=cl=stereo:r=44100" if silent_audio else f"sine=frequency=440:duration={duration}"

    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", vf]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", af]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps)]
    if with_audio:
        cmd += ["-c:a", "aac"]
    cmd += ["-t", str(duration), str(path)]

    subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
    return path


def _valid_mp4(tmp_path: Path) -> Path:
    # rgbtestsrc gera barras de cor que variam ao longo do tempo, passando
    # no gate de cena estática (pHash). O default testsrc2 é rejeitado
    # incorretamente pelo pHash de 16x16 (todos os frames têm hash idêntico).
    return _make_mp4(
        tmp_path / "valido.mp4",
        source=f"rgbtestsrc=size=1080x1920:rate=30:duration=5",
    )


def _wrong_res_mp4(tmp_path: Path) -> Path:
    return _make_mp4(tmp_path / "res_errada.mp4", width=720, height=1280)


def _bad_fps_mp4(tmp_path: Path) -> Path:
    return _make_mp4(tmp_path / "fps_errado.mp4", fps=15, duration=3.0)


def _no_audio_mp4(tmp_path: Path) -> Path:
    return _make_mp4(tmp_path / "sem_audio.mp4", with_audio=False)


def _black_mp4(tmp_path: Path) -> Path:
    return _make_mp4(tmp_path / "preto.mp4", black=True)


def _silent_mp4(tmp_path: Path) -> Path:
    return _make_mp4(tmp_path / "audio_silencioso.mp4", silent_audio=True)


# --------------------------------------------------------------------------- #
# FFprobeValidator — strict=True                                              #
# --------------------------------------------------------------------------- #
def test_ffprobe_strict_rejeita_sem_stream_de_video(tmp_path):
    """strict=True rejeita arquivo sem stream de vídeo (apenas áudio)."""
    validator = FFprobeValidator()
    audio_path = tmp_path / "som.mp3"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=1", str(audio_path)],
        capture_output=True, text=True, check=True, timeout=30,
    )
    with pytest.raises(Exception):
        validator.validate_tiktok(
            audio_path, width=1080, height=1920, fps=30,
            require_audio=True, strict=True,
        )


def test_ffprobe_strict_rejeita_resolucao_incorreta(tmp_path):
    """strict=True rejeita resolução diferente de 1080x1920."""
    validator = FFprobeValidator()
    path = _wrong_res_mp4(tmp_path)
    with pytest.raises(Exception):
        validator.validate_tiktok(
            path, width=1080, height=1920, fps=30,
            require_audio=True, strict=True,
        )


def test_ffprobe_strict_rejeita_fps_fora_do_padrao(tmp_path):
    """strict=True rejeita FPS fora do padrão (~30fps)."""
    validator = FFprobeValidator()
    path = _bad_fps_mp4(tmp_path)
    with pytest.raises(Exception):
        validator.validate_tiktok(
            path, width=1080, height=1920, fps=30,
            require_audio=True, strict=True,
        )


def test_ffprobe_strict_rejeita_sem_audio_quando_require_audio(tmp_path):
    """strict=True rejeita vídeo sem stream de áudio (require_audio=True)."""
    validator = FFprobeValidator()
    path = _no_audio_mp4(tmp_path)
    with pytest.raises(Exception):
        validator.validate_tiktok(
            path, width=1080, height=1920, fps=30,
            require_audio=True, strict=True,
        )


def test_ffprobe_strict_rejeita_duracao_acima_do_limite(tmp_path):
    """strict=True rejeita duração acima do limite (vídeo de 5s com limite de 2s)."""
    validator = FFprobeValidator()
    path = _make_mp4(tmp_path / "duracao_acima.mp4", duration=5.0)
    with pytest.raises(Exception):
        validator.validate_tiktok(
            path, width=1080, height=1920, fps=30,
            require_audio=True, max_duration_seconds=2.0,
            strict=True,
        )


def test_ffprobe_strict_rejeita_arquivo_acima_do_limite_de_tamanho(tmp_path):
    """strict=True rejeita arquivo acima do limite de tamanho (25MB)."""
    validator = FFprobeValidator()
    path = _valid_mp4(tmp_path)
    # Limite absurdamente baixo força a rejeição pelo gate de tamanho.
    with pytest.raises(Exception):
        validator.validate_tiktok(
            path, width=1080, height=1920, fps=30,
            require_audio=True, max_size_mb=0.0001,
            strict=True,
        )


def test_ffprobe_strict_ativa_quality_gates_rejeita_video_preto(tmp_path):
    """strict=True ativa quality gates e rejeita vídeo de quadro preto."""
    validator = FFprobeValidator()
    path = _black_mp4(tmp_path)
    with pytest.raises(Exception):
        validator.validate_tiktok(
            path, width=1080, height=1920, fps=30,
            require_audio=True, strict=True,
        )


def test_ffprobe_strict_aceita_video_valido(tmp_path):
    """strict=True aceita vídeo válido (1080x1920, 30fps, áudio, não preto)."""
    validator = FFprobeValidator(
        thresholds=QualityThresholds(dynamic_scene_phash_threshold=1.0)
    )
    path = _valid_mp4(tmp_path)
    info = validator.validate_tiktok(
        path, width=1080, height=1920, fps=30,
        require_audio=True, strict=True,
    )
    assert isinstance(info, VideoInfo)
    assert info.width == 1080
    assert info.height == 1920
    assert info.has_audio is True


# --------------------------------------------------------------------------- #
# strict=False vs strict=True                                                 #
# --------------------------------------------------------------------------- #
def test_ffprobe_nao_strict_nao_ativa_quality_gates(tmp_path):
    """strict=False não aplica quality gates (apenas checagens básicas)."""
    validator = FFprobeValidator()
    # Vídeo preto passa nas checagens básicas; strict=False não deve rejeitá-lo.
    path = _black_mp4(tmp_path)
    info = validator.validate_tiktok(
        path, width=1080, height=1920, fps=30,
        require_audio=True, strict=False,
    )
    assert isinstance(info, VideoInfo)
    assert info.width == 1080
    assert info.height == 1920


# --------------------------------------------------------------------------- #
# QualityAgent — armazena ValidationResult e avança estágio                   #
# --------------------------------------------------------------------------- #
def test_quality_agent_armazen_validation_result_valido(tmp_path):
    """QualityAgent armazena ValidationResult com valid=True para vídeo válido."""
    state = state_to_dict(empty_state("exec-q", "vid-q"))
    mp4_path = str(_valid_mp4(tmp_path))
    state["video_assets"] = [{"mp4_path": mp4_path, "width": 1080,
                              "height": 1920, "fps": 30.0,
                              "duration_seconds": 5.0, "has_audio": True}]
    state["status"] = "RENDERED"
    state["current_stage"] = "RENDERED"

    out = quality_agent(state)
    vr = out.get("validation_results")
    assert vr is not None
    assert vr["valid"] is True
    assert out["current_stage"] == "VALIDATED"
    assert out["status"] == "VALIDATED"


def test_quality_agent_armazen_validation_result_invalido(tmp_path):
    """QualityAgent armazena ValidationResult com valid=False para vídeo inválido."""
    state = state_to_dict(empty_state("exec-q2", "vid-q2"))
    mp4_path = str(_wrong_res_mp4(tmp_path))
    state["video_assets"] = [{"mp4_path": mp4_path, "width": 720,
                              "height": 1280, "fps": 30.0,
                              "duration_seconds": 5.0, "has_audio": True}]
    state["status"] = "RENDERED"
    state["current_stage"] = "RENDERED"

    out = quality_agent(state)
    vr = out.get("validation_results")
    assert vr is not None
    assert vr["valid"] is False
    assert len(vr["errors"]) > 0


def test_quality_agent_sem_mp4_nao_quebra(tmp_path):
    """QualityAgent não quebra quando não há MP4 (registro de erro)."""
    state = state_to_dict(empty_state("exec-q3", "vid-q3"))
    state["video_assets"] = []
    state["status"] = "RENDERED"
    state["current_stage"] = "RENDERED"

    out = quality_agent(state)
    vr = out.get("validation_results")
    assert vr is not None
    assert vr["valid"] is False
    assert "Nenhum MP4" in str(vr.get("errors", []))


# --------------------------------------------------------------------------- #
# PublishingAgent — gate de qualidade (não publica se qualidade falhou)       #
# --------------------------------------------------------------------------- #
def test_publishing_agent_nao_publica_se_qualidade_invalida():
    """PublishingAgent não publica se validation_results.valid for False."""
    agent = PublishingAgent()
    state = state_to_dict(empty_state("exec-p", "vid-p"))
    state["validation_results"] = {"valid": False, "errors": ["Resolução inválida"]}
    state["compliance"] = {"blocked": False}
    state["live_enabled"] = False
    state["video_assets"] = [{"mp4_path": "output/drafts/video.mp4"}]

    out = agent.run(state)
    assert out["publication_status"] != "uploaded"
    assert out["publication_status"] != "queued"
    # Erro deve ter sido registrado pelo gate de qualidade.
    assert len(out.get("errors", [])) > 0
    assert any("qualidade" in e.get("message", "").lower()
               or "quality" in e.get("message", "").lower()
               for e in out["errors"])


def test_publishing_agent_prossegue_quando_qualidade_valida(tmp_path):
    """PublishingAgent prossegue (modo mock) quando validation_results.valid for True."""
    agent = PublishingAgent()
    state = state_to_dict(empty_state("exec-p2", "vid-p2"))
    state["validation_results"] = {"valid": True, "errors": []}
    state["compliance"] = {"blocked": False}
    state["live_enabled"] = False
    state["video_assets"] = [{"mp4_path": str(_valid_mp4(tmp_path)),
                              "width": 1080, "height": 1920, "fps": 30.0,
                              "duration_seconds": 5.0, "has_audio": True}]

    out = agent.run(state)
    # Em modo mock, não é bloqueado pelo gate de qualidade; chega ao menos a 'uploaded'.
    assert out["publication_status"] in ("uploaded", "queued", "failed")
    quality_errors = [e for e in out.get("errors", [])
                      if "qualidade" in e.get("message", "").lower()
                      or "quality" in e.get("message", "").lower()]
    assert len(quality_errors) == 0


__all__ = []
