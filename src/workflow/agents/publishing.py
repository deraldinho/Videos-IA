"""Agente de publicação: upload Cloudinary + agendamento Buffer (mock sem live)."""

from __future__ import annotations

from typing import Any
from pathlib import Path

from src.workflow.agents.base import BaseAgent
from src.workflow.state import PublicationStatus, Stage


class PublishingAgent(BaseAgent):
    """Publica o vídeo aprovado: upload no Cloudinary + agendamento no Buffer.

    Responsabilidades (CP09):
    - Consome ``video_assets``, ``script`` e ``compliance`` do estado.
    - Faz upload real apenas quando ``live_enabled=True``; caso contrário,
      registra como mock e năo avança para ``PUBLISHED``.
    - Separa os estados: ``UPLOADED`` (após Cloudinary), ``QUEUED`` (após
      Buffer) e ``PUBLISHED`` apenas com confirmaçăo remota real.
    - Cria post agendado no Buffer (mock sem credenciais).
    """

    def __init__(self) -> None:
        super().__init__("PublishingAgent")

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        state = self._ensure_state(state)
        video_id = state["video_id"]
        video_assets = state.get("video_assets") or []
        script = state.get("script") or {}
        compliance = state.get("compliance") or {}

        if compliance.get("blocked"):
            return self._add_error(state, "publish",
                                   "Publicação bloqueada pelo agente de conformidade")

        mp4_path = None
        if video_assets:
            mp4_path = video_assets[-1].get("mp4_path")

        if not mp4_path or not Path(mp4_path).exists():
            return self._add_error(state, "publish",
                                   f"MP4 não encontrado para upload: {mp4_path}")

        live = bool(state.get("live_enabled", False))
        mock = not live

        # --- Upload Cloudinary (real apenas em modo live) -------------------
        try:
            from src.integrations.cloudinary_client import CloudinaryClient  # import local

            cloudinary = CloudinaryClient(live=live)
            upload_result = cloudinary.upload(video_id, Path(mp4_path))
            cloudinary_url = upload_result.secure_url
            cloudinary_id = upload_result.public_id
        except Exception as exc:  # noqa: BLE001
            return self._add_error(state, "publish",
                                   f"Falha no upload Cloudinary: {exc}")

        # Após Cloudinary: estado UPLOADED (nunca PUBLISHED ainda).
        state["cloudinary_url"] = cloudinary_url
        state["cloudinary_id"] = cloudinary_id
        state["publication_status"] = "uploaded"
        state["status"] = "UPLOADED"
        state["current_stage"] = "UPLOADED"

        # --- Agendamento Buffer (real apenas em modo live) ------------------
        try:
            from src.integrations.buffer_client import BufferClient  # import local

            buffer = BufferClient(
                live=live,
                channel_id=state.get("buffer_channel_id") or "mock-tiktok-channel",
            )
            caption = script.get("description", "") + " " + " ".join(
                script.get("hashtags", [])
            )
            post = buffer.create_post(
                video_id=video_id,
                text=caption,
                video_url=cloudinary_url,
            )
            buffer_post_id = post.post_id
            buffer_post_state = post.status
        except Exception as exc:  # noqa: BLE001
            return self._add_error(state, "publish",
                                   f"Falha no agendamento Buffer: {exc}")

        # Após Buffer: estado QUEUED (post agendado).
        state["buffer_post_id"] = buffer_post_id
        state["buffer_post_state"] = buffer_post_state
        state["publication_status"] = "queued"
        state["status"] = "QUEUED"
        state["current_stage"] = "QUEUED"
        state["_next"] = "supervisor"
        state["_step"] = "publishing_agent"
        self._add_cost(state, self.name, "publicacao", tokens=0, cost_usd=0.0)
        return self._mark(state, self.name, "publish",
                          f"Processado: Cloudinary={cloudinary_id}, Buffer={buffer_post_id} "
                          f"(mock={mock})",
                          video_id=video_id, cloudinary_url=cloudinary_url,
                          buffer_post_id=buffer_post_id, mock=mock)
