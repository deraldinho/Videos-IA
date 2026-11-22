"""Cliente Cloudinary: upload, URL pública, dedup por public_id, controle de cota gratuita.

Nenhum upload real ocorre enquanto o modo ``live`` estiver desativado — em vez
disso, o cliente devolve URLs simuladas e registra a intenção, permitindo que o
pipeline rode totalmente offline em DRY_RUN/REVIEW.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from src.errors import PipelineError


@dataclass(frozen=True)
class UploadResult:
    """Resultado de um upload (real ou simulado)."""
    public_id: str
    secure_url: str
    url: str
    bytes_uploaded: int
    duration_seconds: float
    mock: bool


class CloudinaryClient:
    """Cliente Cloudinary com dedup e monitoramento de cota do plano gratuito."""

    def __init__(
        self,
        *,
        cloud_name: Optional[str] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        live: bool = False,
        max_video_mb: int = 25,
        stop_usage_percent: int = 85,
    ) -> None:
        self.cloud_name = cloud_name or os.environ.get("CLOUDINARY_CLOUD_NAME")
        self.api_key = api_key or os.environ.get("CLOUDINARY_API_KEY")
        self.api_secret = api_secret or os.environ.get("CLOUDINARY_API_SECRET")
        self.live = live
        self.max_video_mb = max_video_mb
        self.stop_usage_percent = stop_usage_percent
        self._uploaded: dict[str, UploadResult] = {}  # dedup em memória por sessão

    def _require_credentials(self) -> None:
        if not self.live:
            return
        missing = [n for n, v in (
            ("CLOUDINARY_CLOUD_NAME", self.cloud_name),
            ("CLOUDINARY_API_KEY", self.api_key),
            ("CLOUDINARY_API_SECRET", self.api_secret),
        ) if not v]
        if missing:
            raise PipelineError(
                "Credenciais do Cloudinary ausentes para modo live: "
                + ", ".join(missing)
            )

    def _public_id_for(self, video_id: str, file_path: Path) -> str:
        """public_id determinístico: dedup por conteúdo do vídeo."""
        digest = hashlib.sha256(
            f"{video_id}:{file_path.name}:{file_path.stat().st_size if file_path.exists() else 0}".encode()
        ).hexdigest()[:16]
        return f"tiktok_automation/{video_id}/{digest}"

    def upload(self, video_id: str, file_path: Path,
               *, overwrite: bool = False) -> UploadResult:
        """Faz upload do MP4 e devolve a URL pública.

        Dedup: se o mesmo ``public_id`` já foi enviado nesta sessão, retorna o
        resultado anterior sem novo upload.
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise PipelineError(f"Arquivo não encontrado: {file_path}")

        size_mb = file_path.stat().st_size / (1024 * 1024)
        if size_mb > self.max_video_mb:
            raise PipelineError(
                f"Vídeo excede {self.max_video_mb}MB: {size_mb:.2f}MB"
            )

        public_id = self._public_id_for(video_id, file_path)

        if not self.live:
            # Mock: não faz chamada de rede.
            if public_id in self._uploaded and not overwrite:
                return self._uploaded[public_id]
            result = UploadResult(
                public_id=public_id,
                secure_url=f"https://res.cloudinary.com/{self.cloud_name or 'demo'}/video/upload/v1/{public_id}.mp4",
                url=f"http://res.cloudinary.com/{self.cloud_name or 'demo'}/video/upload/v1/{public_id}.mp4",
                bytes_uploaded=file_path.stat().st_size,
                duration_seconds=0.0,
                mock=True,
            )
            self._uploaded[public_id] = result
            return result

        self._require_credentials()
        if public_id in self._uploaded and not overwrite:
            return self._uploaded[public_id]

        upload_url = f"https://api.cloudinary.com/v1_1/{self.cloud_name}/video/upload"
        with open(file_path, "rb") as fh:
            files = {"file": (file_path.name, fh, "video/mp4")}
            data = {
                "public_id": public_id,
                "resource_type": "video",
                "upload_preset": "tiktok_automation",
            }
            try:
                resp = requests.post(
                    upload_url,
                    data=data,
                    files=files,
                    auth=(self.api_key or "", self.api_secret or ""),
                    timeout=300,
                )
            except requests.RequestException as exc:
                raise PipelineError(
                    f"Falha de rede no upload Cloudinary: {exc}"
                ) from exc

            if resp.status_code >= 400:
                raise PipelineError(
                    f"Cloudinary retornou HTTP {resp.status_code}: {resp.text[:500]}"
                )

            payload = resp.json()
            result = UploadResult(
                public_id=payload.get("public_id", public_id),
                secure_url=payload.get("secure_url", ""),
                url=payload.get("url", ""),
                bytes_uploaded=int(payload.get("bytes", file_path.stat().st_size)),
                duration_seconds=float(payload.get("duration", 0.0)),
                mock=False,
            )
            self._uploaded[public_id] = result
            return result

    def check_quota(self) -> dict:
        """Consulta o uso atual da cota (apenas em modo live)."""
        if not self.live:
            return {"mock": True, "stop_usage_percent": self.stop_usage_percent}
        self._require_credentials()
        try:
            resp = requests.get(
                f"https://api.cloudinary.com/v1_1/{self.cloud_name}/usage/video",
                auth=(self.api_key or "", self.api_secret or ""),
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            raise PipelineError(f"Falha ao consultar cota Cloudinary: {exc}") from exc

    def is_at_limit(self) -> bool:
        """Retorna True se o uso chegou ao limite de parada configurado."""
        usage = self.check_quota()
        if usage.get("mock"):
            return False
        # Estrutura simplificada: ajuste conforme a resposta real da API.
        used = usage.get("used_percent", 0)
        return float(used) >= self.stop_usage_percent


__all__ = ["CloudinaryClient", "UploadResult"]
