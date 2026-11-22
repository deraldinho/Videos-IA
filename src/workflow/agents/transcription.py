"""Agente de transcrição/ASR: transcreve o áudio gerado com ASR real."""

from __future__ import annotations

from typing import Any

from src.asr.adapter import ASRAdapter
from src.workflow.agents.base import BaseAgent
from src.workflow.state import Stage


class TranscriptionAgent(BaseAgent):
    """Transcreve o áudio do vídeo renderizado (ASR) para verificação de qualidade.

    Responsabilidades (CP08):
    - Consome ``audio_assets`` (WAVs de narração) do estado.
    - Usa ``ASRAdapter`` para transcrever o áudio REAL (Parakeet/Whisper ou
      análise de áudio com FFmpeg quando nenhum motor ASR está instalado).
    - Nunca copia o roteiro como se fosse transcrição.
    - Avança current_stage para TRANSCRIBED.
    """

    def __init__(self) -> None:
        super().__init__("TranscriptionAgent")

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        state = self._ensure_state(state)
        audio_assets = state.get("audio_assets") or []

        # Encontra o primeiro WAV disponível para transcrever.
        wav_path: str | None = None
        for asset in audio_assets:
            path = asset.get("wav_path", "") if isinstance(asset, dict) else ""
            if path and __import__("pathlib").Path(path).exists():
                wav_path = path
                break

        if not wav_path:
            return self._add_error(
                state, "transcription",
                "Nenhum arquivo de áudio (WAV) disponível para transcrever."
            )

        asr = ASRAdapter()
        try:
            result = asr.transcribe(__import__("pathlib").Path(wav_path))
        except Exception as exc:  # noqa: BLE001
            return self._add_error(state, "transcription",
                                   f"Falha na transcrição ASR: {exc}")

        transcription = {
            "text": result.text,
            "confidence": result.confidence,
            "engine": result.engine,
            "segments": result.segments,
            "duration_seconds": result.duration_seconds,
            "has_audio": result.has_audio,
            "wav_path": wav_path,
        }

        state["transcription"] = transcription
        state["current_stage"] = "TRANSCRIBED"
        state["status"] = "TRANSCRIBED"
        state["_next"] = "supervisor"
        state["_step"] = "transcription_agent"
        tokens = len(result.text) if result.text else 0
        self._add_cost(state, self.name, "asr", tokens=tokens, cost_usd=0.0)
        return self._mark(state, self.name, "transcription",
                          f"Transcrito via {result.engine} "
                          f"({len(result.segments)} segmentos, conf={result.confidence})",
                          video_id=state["video_id"],
                          engine=result.engine, confidence=result.confidence)
