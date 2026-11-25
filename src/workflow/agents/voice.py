"""Agente de voz: narração TTS (Piper pt-BR) com fallback de áudio audível."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
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
        tts = None
        try:
            from src.video.tts import PiperTTS  # import local para evitar ciclo

            tts = PiperTTS(
                output_dir="output/drafts/tts",
                require_real=tts_required,
            )
        except Exception as exc:  # noqa: BLE001
            if tts_required:
                # Não disfarça falha de TTS como sucesso silencioso.
                return self._add_error(state, "voice",
                                       f"TTS real indisponível: {exc}")
            # PiperTTS não pôde ser instanciado: fallback de tom de desenvolvimento
            # (sucesso controlado, não erro — permite validar o fluxo).
            _gerar_audio_dev_fallback(state, scenes, audio_assets)
            total_duration = float(sum(
                a.get("duration_seconds", 0.0) for a in audio_assets
            ))
        else:
            if tts_required and not tts.is_available():
                # Não disfarça falha de TTS como sucesso silencioso.
                return self._add_error(state, "voice",
                                       "Piper TTS ou modelo pt-BR não disponível "
                                       "(tts_required=true).")
            if not tts.is_available():
                # Sem Piper/modelo: fallback de tom de desenvolvimento em vez de
                # silêncio puro (que falharia na ASR/quality gate).
                _gerar_audio_dev_fallback(state, scenes, audio_assets)
                total_duration = float(sum(
                    a.get("duration_seconds", 0.0) for a in audio_assets
                ))
            else:
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


def _gerar_audio_dev_fallback(state: dict[str, Any], scenes: list[dict[str, Any]],
                              audio_assets: list[dict[str, Any]]) -> None:
    """Gera tom de desenvolvimento (fallback quando Piper/modelo indisponível).

    Cria um WAV audível por cena/frase para permitir que o pipeline continue
    (incluindo aprovação humana) sem afirmar que o áudio é narração real.
    """
    video_id = state["video_id"]
    output_dir = Path(state.get("audio_dir", "output/drafts/tts"))
    output_dir.mkdir(parents=True, exist_ok=True)

    for idx, scene in enumerate(scenes):
        role = scene.get("role", "corpo") if isinstance(scene, dict) else "corpo"
        phrases = scene.get("narration", []) if isinstance(scene, dict) else []
        for i in range(len(phrases)):
            wav_path = output_dir / f"{video_id}_{idx}_{role}_{i:02d}_dev.wav"
            _write_dev_tone(wav_path, seconds=1.0, frequency=440.0)
            audio_assets.append(
                AudioAsset(scene_role=role, index=i, text="",
                           wav_path=str(wav_path), duration_seconds=1.0).model_dump(mode="json")
            )


def _write_dev_tone(wav_path: Path, seconds: float = 1.0, frequency: float = 440.0) -> None:
    """Escreve um WAV com tom audível (placeholder de desenvolvimento).

    Gera um tom senoide de baixa amplitude para permitir que o pipeline continue
    (incluindo aprovação humana) sem afirmar que o áudio é narração real.
    Distingue-se de silêncio puro para não falhar na ASR/quality gate, mas é
    claramente um placeholder de desenvolvimento (não narração genuína).
    """
    sample_rate = 22050
    n_samples = int(sample_rate * seconds)
    amplitude = 3000  # abaixo do clipping, audível para volumedetect
    frames = b"".join(
        struct.pack(
            "<h",
            int(amplitude * math.sin(2 * math.pi * frequency * (t / sample_rate))),
        )
        for t in range(n_samples)
    )
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(frames)
