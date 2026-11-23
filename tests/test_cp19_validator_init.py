"""Regressao CP19: Orchestrator deve inicializar self.validator.

Defeito: Orchestrator.__init__ importava FFprobeValidator mas nunca instanciava
self.validator, causando AttributeError quando _stage_validate chamava
self.validator.validate_tiktok(...).
"""

from src.orchestrator import Orchestrator
from src.video.validator import FFprobeValidator


def test_orchestrator_initializes_validator():
    """self.validator deve ser uma instancia de FFprobeValidator apos init."""
    orch = Orchestrator()
    try:
        assert isinstance(orch.validator, FFprobeValidator)
    finally:
        orch.close()


def test_orchestrator_validator_can_call_validate_tiktok(monkeypatch, tmp_path):
    """validate_tiktok deve ser acessivel via self.validator sem AttributeError."""
    orch = Orchestrator()
    try:
        # Criar um arquivo MP4 falso minimo apenas para exercer o caminho do metodo.
        # Como ffprobe pode nao estar instalado, mockamos validate_tiktok para
        # verificar que o metodo e acessavel (nao lança AttributeError).
        called = {}

        def fake_validate(self, path, *, strict=False):
            called["strict"] = strict
            from src.video.validator import VideoInfo

            return VideoInfo(
                path=path,
                width=1080,
                height=1920,
                fps=30.0,
                duration_seconds=10.0,
                has_audio=True,
                codec="h264",
                format_name="mov,mp4,m4a,3gp,3g2,mj2",
                size_bytes=1000,
                caption_streams=0,
            )

        monkeypatch.setattr(FFprobeValidator, "validate_tiktok", fake_validate)

        result = orch.validator.validate_tiktok(
            tmp_path / "fake.mp4", strict=True
        )
        assert called["strict"] is True
        assert result.width == 1080
        assert result.height == 1920
        assert result.has_audio is True
    finally:
        orch.close()
