"""Agente de voz: narração TTS (Piper pt-BR) com fallback de silêncio."""

from __future__ import annotations

from typing import Any

from src.workflow.agents.base import BaseAgent
from src.workflow.state import AudioAsset, Stage


class VoiceAgent(BaseAgent):
    """Gera a narração TTS (Piper pt-BR) para cada cena do roteiro.

    Responsabilidades (CP06):
    - Consome ``script`` do estado.
    - Usa ``PiperTTS`` para sintetizar cada frase.
    - Quando ``tts_required`` está ativo no estado e o Piper/modelo não estão
      disponíveis, levanta erro claro em vez de gravar silêncio disfarçado.
    - Armazena ``audio_assets`` (lista de AudioAsset) no estado.
    - Avança current_stage para VOICE_READY apenas se houver áudio real.
    """

    def __init__(self, require_real_tts: bool = False) -> None:
        super().__init__("VoiceAgent")
        self.require_real_tts = require_real_tts

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        state = self._ensure_state(state)
        script = state.get("script")
        if not script:
            return self._add_error(state, "voice", "Sem roteiro para gerar narração.")

        scenes = script.get("scenes", []) if isinstance(script, dict) else []
        audio_assets: list[dict[str, Any]] = []
        total_duration = 0.0

        tts_required = self.require_real_tts or bool(state.get("tts_required", False))

        # Tenta usar o PiperTTS real.
        try:
            from src.video.tts import PiperTTS  # import local para evitar ciclo

            tts = PiperTTS(
                output_dir="output/drafts/tts",
                require_real=tts_required,
            )
            if tts_required and not tts.is_available():
                raise PipelineError(
                    "Piper TTS ou modelo pt-BR não disponível (tts_required=true)."
                )
            for idx, scene in enumerate(scenes):
                role = scene.get("role", "corpo") if isinstance(scene, dict) else "corpo"
                phrases = scene.get("narration", []) if isinstance(scene, dict) else []
                for i, phrase in enumerate(phrases):
                    segments = tts.synthesize_scene(role, [phrase], i,
                                                    prefix=f"{state['video_id']}_{idx}")
                    for seg in segments:
                        entry = AudioAsset(
                            scene_role=seg.scene_role,
                            index=seg.index,
                            text=seg.text,
                            wav_path=str(seg.wav_path),
                            duration_seconds=seg.duration_seconds,
                        )
                        audio_assets.append(entry.model_dump(mode="json"))
                        total_duration += seg.duration_seconds
        except Exception as exc:  # noqa: BLE001
            if tts_required:
                # Não disfarça falha de TTS como sucesso silencioso.
                return self._add_error(state, "voice",
                                       f"TTS real indisponível: {exc}")
            # Fallback de silêncio apenas quando tts_required não está ativo.
            audio_assets.append(
                AudioAsset(scene_role="narracao", index=0, text="",
                           wav_path="", duration_seconds=1.0).model_dump(mode="json")
            )
            total_duration = 1.0
            state = self._add_error(state, "voice", f"TTS fallback (silêncio): {exc}")

        if not audio_assets:
            return self._add_error(state, "voice", "Nenhum segmento de áudio gerado.")

        state["audio_assets"] = audio_assets
        state["current_stage"] = "VOICE_READY"
        state["status"] = "VOICE_READY"
        state["_next"] = "supervisor"
        state["_step"] = "voice_agent"
        self._add_cost(state, self.name, "tts", tokens=0, cost_usd=0.0)
        return self._mark(state, self.name, "voice",
                          f"{len(audio_assets)} segmentos de áudio ({total_duration:.1f}s)",
                          video_id=state["video_id"], duration=total_duration)
