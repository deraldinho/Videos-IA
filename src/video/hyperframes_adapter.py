"""Adaptador desacoplado para HyperFrames (HeyGen) com fallback para renderer próprio.

Tenta renderizar usando o HyperFrames CLI (``vendor/hyperframes/packages/cli``)
quando disponível. Caso contrário, delega ao ``HTMLRenderer`` existente
(Chromium headless + FFmpeg), preservando a mesma interface de renderização.

Não há dependência direta do pacote Python ``hyperframes`` (que é um pacote
de HTTP/2 frames, não relacionado a vídeo). A integração é feita via CLI
Node.js do repositório clonado em ``vendor/hyperframes``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Mapping, Optional

from src.errors import PipelineError
from src.logging import get_logger
from src.settings import Settings, load_settings
from src.video.renderer import HTMLRenderer

logger = get_logger(__name__)


class HyperFramesAdapter:
    """Renderiza HTML/CSS para MP4 via HyperFrames com fallback para renderer próprio.

    Responsabilidades (CP02 / Prioridade 2):
    - Desacoplado: não importa nem depende do HyperFrames diretamente.
    - Tenta o CLI do HyperFrames (vendor/hyperframes/packages/cli) quando instalado.
    - Fallback automático para HTMLRenderer (Chromium + FFmpeg) se HyperFrames
      não estiver disponível ou falhar.
    - Nunca silencia falhas: levanta PipelineError com mensagem clara.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        vendor_dir: Path | str | None = None,
        node_bin: str = "node",
        ffmpeg_bin: str = "ffmpeg",
        output_dir: Path | str = "output/drafts",
    ) -> None:
        self.settings = settings
        self.node_bin = node_bin
        self.ffmpeg_bin = ffmpeg_bin
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Diretório do vendor/hyperframes (repositório clonado do HeyGen).
        if vendor_dir is None:
            vendor_dir = Path(__file__).resolve().parents[1] / "vendor" / "hyperframes"
        self.vendor_dir = Path(vendor_dir).resolve()

        self.settings = settings
        if settings is not None:
            self.video_width = int(settings.video.width)
            self.video_height = int(settings.video.height)
            self.video_fps = int(settings.video.fps)
        else:
            self.video_width = 1080
            self.video_height = 1920
            self.video_fps = 30

        self._cli_bin = self._find_hyperframes_cli()
        self._fallback_renderer = HTMLRenderer(
            node_bin=node_bin,
            ffmpeg_bin=ffmpeg_bin,
            templates_dir="templates",
            output_dir=self.output_dir,
            settings=self.settings,
        )

    # ------------------------------------------------------------------ #
    # Descoberta do HyperFrames                                          #
    # ------------------------------------------------------------------ #
    def _find_hyperframes_cli(self) -> Optional[str]:
        """Localiza o executável CLI do HyperFrames (vendor ou npm global)."""
        # 1. Vendor clonado: packages/cli/dist/index.js ou src/index.ts
        cli_candidates = [
            self.vendor_dir / "packages" / "cli" / "dist" / "index.js",
            self.vendor_dir / "packages" / "cli" / "dist" / "cli.js",
            self.vendor_dir / "packages" / "cli" / "bin" / "hyperframes",
            self.vendor_dir / "packages" / "cli" / "package.json",
        ]
        for c in cli_candidates:
            if c.exists():
                return str(c)

        # 2. npm global: `hyperframes` no PATH
        resolved = shutil.which("hyperframes")
        if resolved:
            return resolved

        return None

    @property
    def is_available(self) -> bool:
        """Retorna True se o HyperFrames CLI está disponível."""
        return self._cli_bin is not None

    # ------------------------------------------------------------------ #
    # Entrada principal                                                  #
    # ------------------------------------------------------------------ #
    def render(
        self,
        template_name: str,
        context: Mapping[str, object],
        duration_seconds: float,
        output_name: Optional[str] = None,
        audio_wav: Optional[Path] = None,
    ) -> Path:
        """Renderiza o template e devolve o caminho do MP4 gerado.

        Tenta HyperFrames primeiro; se não disponível ou falhar, usa o
        HTMLRenderer (fallback). Nunca produz placeholder silencioso sem
        avisar: levanta PipelineError se nenhum caminho funcionar.
        """
        output_name = output_name or f"render_{uuid.uuid4().hex[:8]}.mp4"
        output_path = self.output_dir / output_name

        # --- Caminho 1: HyperFrames CLI -------------------------------- #
        if self.is_available:
            try:
                return self._render_with_hyperframes(
                    template_name, context, duration_seconds, output_path, audio_wav
                )
            except Exception as exc:  # noqa: BLE001
                # HyperFrames falhou: loga o erro com rastreio completo e cai no fallback.
                # Nunca silencia: a falha fica visível nos logs antes do fallback.
                logger.exception(
                    "HyperFrames falhou ao renderizar o template '%s' (%s): "
                    "caindo para o fallback HTMLRenderer.",
                    template_name,
                    exc,
                )

        # --- Caminho 2: Fallback renderer próprio (Chromium + FFmpeg) -- #
        return self._fallback_renderer.render(
            template_name, context, duration_seconds,
            output_name=output_name, audio_wav=audio_wav,
        )

    # ------------------------------------------------------------------ #
    # Renderização via HyperFrames CLI                                   #
    # ------------------------------------------------------------------ #
    def _render_with_hyperframes(
        self,
        template_name: str,
        context: Mapping[str, object],
        duration_seconds: float,
        output_path: Path,
        audio_wav: Optional[Path],
    ) -> Path:
        """Invoca o CLI do HyperFrames para renderizar o template."""
        # Gera um arquivo HTML temporário com o contexto injetado.
        html_path = self.vendor_dir / "templates" / f"{template_name}.html"
        if not html_path.exists():
            html_path = Path("templates") / f"{template_name}.html"
        html = html_path.read_text(encoding="utf-8")

        # Substitui variáveis {{chave}} (mesma convenção do renderer próprio).
        for key, value in context.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                replacement = " ".join(str(v) for v in value)
            else:
                replacement = str(value)
            html = html.replace("{{" + key + "}}", replacement)

        context_json = json.dumps(context, ensure_ascii=False)
        injected = (
            "<script>\nwindow.__VIDEO_CONTEXT__ = " + context_json + ";\n</script>\n" + html
        )
        rendered_html = self.output_dir / f"hf_rendered_{uuid.uuid4().hex[:8]}.html"
        rendered_html.write_text(injected, encoding="utf-8")

        # Monta comando do CLI HyperFrames.
        # O comando exato depende da versão instalada; tentamos `render` com
        # entrada de arquivo HTML e saída MP4.
        cli = self._cli_bin
        tmp_html = rendered_html
        cmd = [
            self.node_bin, cli, "render",
            str(tmp_html),
            "--output", str(output_path),
            "--duration", str(duration_seconds),
            "--fps", str(self.video_fps),
            "--width", str(self.video_width),
            "--height", str(self.video_height),
        ]
        if audio_wav is not None and Path(audio_wav).exists():
            cmd += ["--audio", str(audio_wav)]

        env = os.environ.copy()
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, env=env,
        )
        if proc.returncode != 0:
            raise PipelineError(
                f"HyperFrames CLI falhou (código {proc.returncode}): "
                f"{proc.stderr[-800:]}"
            )

        if not output_path.exists():
            raise PipelineError(
                f"HyperFrames não produziu o MP4 de saída: {output_path}"
            )

        return output_path
