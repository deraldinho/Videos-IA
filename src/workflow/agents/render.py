"""Agente de renderização: HTML + headless browser + FFmpeg -> MP4 real."""

from __future__ import annotations

from typing import Any
from pathlib import Path

from src.settings import load_settings
from src.workflow.agents.base import BaseAgent
from src.workflow.state import Stage, VideoAsset


class RenderAgent(BaseAgent):
    """Renderiza o MP4 final (1080×1920, 30fps, H.264/AAC) a partir do storyboard.

    Responsabilidades (CP07):
    - Consome ``storyboard``, ``audio_assets`` e ``script`` do estado.
    - Usa ``HTMLRenderer`` para gerar o MP4 (real com navegador headless +
      FFmpeg, ou placeholder de fallback).
    - Incorpora o áudio WAV da narração no MP4.
    - Armazena ``video_assets`` (lista de VideoAsset) no estado.
    - Avança current_stage para RENDERED.
    """

    def __init__(self) -> None:
        super().__init__("RenderAgent")
        # Carrega settings internamente (padrao seguro usado nos Findings 5/6).
        # load_settings() tolera ausencia de credenciais em modo REVIEW/DRY_RUN
        # (live_enabled=false), caindo para defaults iguais aos valores
        # anteriormente hardcoded (1080/1920/30) quando config nao sobrescreve.
        self.settings = load_settings()

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        state = self._ensure_state(state)
        storyboard = state.get("storyboard") or {}
        script = state.get("script") or {}
        audio_assets = state.get("audio_assets") or []
        video_id = state["video_id"]

        template_name = storyboard.get("template", "curiosidade")
        total_seconds = float(storyboard.get("total_seconds", 15.0))

        context = {
            "video_id": video_id,
            "topic": state.get("topic") or "",
            "template": template_name,
            "scenes": script.get("scenes", []),
            "title": script.get("title", ""),
            "description": script.get("description", ""),
            "hashtags": script.get("hashtags", []),
            "duration_seconds": total_seconds,
        }

        # Áudio: concatena WAVs dos segmentos ou usa silêncio.
        audio_wav = self._build_narration_wav(audio_assets, video_id)

        try:
            from src.video.renderer import HTMLRenderer  # import local

            renderer = HTMLRenderer(
                output_dir=Path("output/drafts") / "renders",
                settings=self.settings,
            )
            mp4_path = renderer.render(
                template_name,
                context,
                duration_seconds=total_seconds,
                output_name=f"{video_id}.mp4",
                audio_wav=Path(audio_wav) if audio_wav else None,
            )

            # Lê metadados do MP4 gerado.
            size = mp4_path.stat().st_size if mp4_path.exists() else 0
            video_asset = VideoAsset(
                mp4_path=str(mp4_path),
                width=self.settings.video.width,
                height=self.settings.video.height,
                fps=float(self.settings.video.fps),
                duration_seconds=total_seconds,
                has_audio=audio_wav is not None,
                size_bytes=size,
            )
            state["video_assets"] = [video_asset.model_dump(mode="json")]
            state["current_stage"] = "RENDERED"
            state["status"] = "RENDERED"
            state["_next"] = "supervisor"
            state["_step"] = "render_agent"
            self._add_cost(state, self.name, "render", tokens=0, cost_usd=0.0)
            return self._mark(state, self.name, "render",
                              f"MP4 renderizado: {mp4_path.name} ({size} bytes)",
                              video_id=video_id, mp4_path=str(mp4_path), size_bytes=size)
        except Exception as exc:  # noqa: BLE001
            return self._add_error(state, "render", f"Falha na renderização: {exc}")

    def _build_narration_wav(self, audio_assets: list[dict[str, Any]],
                             video_id: str) -> str | None:
        """Concatena os WAVs dos segmentos de áudio num único ficheiro.

        Retorna o caminho do WAV combinado, ou None se não houver segmentos.
        """
        if not audio_assets:
            return None

        import wave

        output_dir = Path("output/drafts") / "tts"
        output_dir.mkdir(parents=True, exist_ok=True)
        combined = output_dir / f"{video_id}_narracao_completa.wav"

        sample_rate = 22050
        gap_frames = b"\x00\x00" * int(sample_rate * 0.3)  # 300 ms entre frases
        with wave.open(str(combined), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            for asset in audio_assets:
                wav_path = asset.get("wav_path")
                if wav_path and Path(wav_path).exists():
                    with wave.open(str(wav_path), "rb") as sf:
                        wf.writeframes(sf.readframes(sf.getnframes()))
                    wf.writeframes(gap_frames)
        return str(combined)
