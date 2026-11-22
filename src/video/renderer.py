"""Renderização de templates HTML/CSS para MP4 via Chromium headless + FFmpeg.

Produz um MP4 real (1080×1920, 30 fps, H.264/AAC) capturando frames de um
template HTML renderizado em navegador headless (Puppeteer/Chromium) e
codificando-os com FFmpeg, incluindo a narração TTS quando disponível.

Não depende do VS Code. Quando nenhum navegador headless nem FFmpeg estão
disponíveis, produz um MP4 de placeholder em modo local apenas para não
bloquear o desenvolvimento — esse fallback é intencionalmente limitado e não
é considerado renderização válida para qualidade de produção.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Mapping, Sequence

from src.errors import PipelineError
from src.settings import Settings, load_settings


class HTMLRenderer:
    """Renderiza um template HTML com dados do roteiro e produz um MP4."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        node_bin: str = "node",
        ffmpeg_bin: str = "ffmpeg",
        templates_dir: Path | str = "templates",
        output_dir: Path | str = "output/drafts",
    ) -> None:
        self.settings = settings
        if settings is None:
            try:
                settings = load_settings()
            except Exception:  # config indisponível: mantém defaults seguros
                settings = None
        self.settings = settings
        if settings is not None:
            self.video_width = int(settings.video.width)
            self.video_height = int(settings.video.height)
            self.video_fps = int(settings.video.fps)
        else:
            # Defaults iguais aos valores anteriormente hardcoded.
            self.video_width = 1080
            self.video_height = 1920
            self.video_fps = 30
        self.node_bin = node_bin
        self.ffmpeg_bin = ffmpeg_bin
        self.templates_dir = Path(templates_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._puppeteer_module = self._resolve_puppeteer_module()

    # ------------------------------------------------------------------ #
    # Descoberta de ferramentas                                          #
    # ------------------------------------------------------------------ #
    def _resolve_puppeteer_module(self) -> str | None:
        """Localiza o módulo Puppeteer instalado (node_modules/puppeteer).

        Sobe a partir deste arquivo até encontrar a raiz do projeto que
        contenha node_modules/puppeteer, para que o capture script consiga
        requerer o módulo independentemente do diretório de trabalho ou do
        output_dir.
        """
        here = Path(__file__).resolve()
        for ancestor in [here.parent] + list(here.parents):
            candidate = ancestor / "node_modules" / "puppeteer"
            if candidate.exists():
                return str(candidate)
        return None

    def _find_node(self) -> str | None:
        resolved = shutil.which(self.node_bin)
        return resolved

    def _find_ffmpeg(self) -> str | None:
        resolved = shutil.which(self.ffmpeg_bin)
        return resolved

    def _find_chromium(self) -> str | None:
        """Localiza um executável Chromium/Chrome para headless.

        Ordem de preferência:
        1. Executável do Chromium bundlado pelo Puppeteer (cache local).
        2. Navegador do sistema (chromium/chrome no PATH ou caminhos Windows).
        """
        # 1. Puppeteer (cache local ~/.cache/puppeteer/chrome/...)
        try:
            home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or str(Path.home())
            cache_root = Path(home) / ".cache" / "puppeteer" / "chrome"
            if cache_root.exists():
                for version_dir in sorted(cache_root.iterdir(), reverse=True):
                    exe = self._find_chrome_exe_in(version_dir)
                    if exe:
                        return exe
        except (OSError, RuntimeError):
            pass

        # 2. Navegador do sistema.
        candidates = [
            "chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
            "chrome",
        ]
        win_candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        for c in candidates + win_candidates:
            if shutil.which(c) or Path(c).exists():
                return c
        return None

    @staticmethod
    def _find_chrome_exe_in(version_dir: Path) -> str | None:
        """Procura chrome.exe dentro de uma pasta de versão do Puppeteer."""
        if not version_dir.is_dir():
            return None
        for candidate in version_dir.rglob("chrome.exe"):
            if candidate.is_file():
                return str(candidate)
        return None

    # ------------------------------------------------------------------ #
    # Entrada principal                                                  #
    # ------------------------------------------------------------------ #
    def render(
        self,
        template_name: str,
        context: Mapping[str, object],
        duration_seconds: float,
        output_name: str | None = None,
        audio_wav: Path | None = None,
    ) -> Path:
        """Renderiza o template e devolve o caminho do MP4 gerado."""
        template_path = self.templates_dir / f"{template_name}.html"
        if not template_path.exists():
            raise PipelineError(f"Template não encontrado: {template_path}")

        output_name = output_name or f"render_{uuid.uuid4().hex[:8]}.mp4"
        output_path = self.output_dir / output_name

        ffmpeg = self._find_ffmpeg()
        node = self._find_node()
        chromium = self._find_chromium()

        missing = []
        if not ffmpeg:
            missing.append("ffmpeg")
        if not node:
            missing.append("node")
        if not chromium:
            missing.append("chromium")
        if missing:
            raise PipelineError(
                "Renderização real requer FFmpeg, Node e Chromium (Puppeteer). "
                f"Faltando: {', '.join(missing)}. Instale as dependências para gerar MP4 de verdade."
            )

        return self._render_with_browser(
            template_path, context, duration_seconds, output_path,
            chromium, node, ffmpeg, audio_wav=audio_wav,
        )

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _substitute(html: str, context: Mapping[str, object]) -> str:
        """Substitui variáveis ``{{chave}}`` do HTML pelos valores do contexto."""
        for key, value in context.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                replacement = " ".join(str(v) for v in value)
            else:
                replacement = str(value)
            html = html.replace("{{" + key + "}}", replacement)
        return html

    # ------------------------------------------------------------------ #
    # Caminho real: HTML → screenshot sequencial → MP4 via FFmpeg        #
    # ------------------------------------------------------------------ #
    def _render_with_browser(
        self,
        template_path: Path,
        context: Mapping[str, object],
        duration_seconds: float,
        output_path: Path,
        chromium: str,
        node: str,
        ffmpeg: str,
        audio_wav: Path | None = None,
    ) -> Path:
        """Captura frames reais do HTML em Chromium headless e codifica MP4."""
        html = template_path.read_text(encoding="utf-8")
        html = self._substitute(html, context)
        context_json = json.dumps(context, ensure_ascii=False)
        injected = (
            "<script>\n"
            "window.__VIDEO_CONTEXT__ = " + context_json + ";\n"
            "</script>\n"
            + html
        )
        rendered_html = self.output_dir / f"rendered_{uuid.uuid4().hex[:8]}.html"
        rendered_html.write_text(injected, encoding="utf-8")

        capture_script = self.output_dir / f"capture_{uuid.uuid4().hex[:8]}.js"
        scenes = context.get("scenes") or []
        html_path_posix = str(rendered_html).replace("\\", "/")
        capture_script.write_text(
            self._capture_script(html_path_posix, duration_seconds, scenes), encoding="utf-8"
        )

        frames_dir = self.output_dir / f"frames_{uuid.uuid4().hex[:8]}"
        frames_dir.mkdir(parents=True, exist_ok=True)

        video_only: Path | None = None
        try:
            env = os.environ.copy()
            chrome_exe = chromium if Path(chromium).exists() else shutil.which(chromium)
            if chrome_exe:
                env["CHROME_EXE"] = chrome_exe
            env["FRAMES_DIR"] = str(frames_dir)
            if self._puppeteer_module:
                env["PUPPETEER_MODULE"] = self._puppeteer_module
            proc = subprocess.run(
                [node, str(capture_script)],
                capture_output=True, text=True, timeout=300, env=env,
            )
            if proc.returncode != 0:
                raise PipelineError(
                    f"Captura de frames falhou: {proc.stderr[-800:]}"
                )

            # Confirma que há frames capturados (sem isso o FFmpeg falharia silenciosamente).
            captured = sorted(frames_dir.glob("frame-*.png"))
            if not captured:
                raise PipelineError(
                    f"Nenhum frame capturado pelo navegador em {frames_dir}"
                )

            # Codifica os frames em MP4 (sem áudio) num arquivo temporário.
            video_only = self.output_dir / f"video_{uuid.uuid4().hex[:8]}.mp4"
            fps = self.video_fps
            pattern = str(frames_dir / "frame-%04d.png")
            cmd = [
                ffmpeg, "-y", "-framerate", str(fps),
                "-i", pattern,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-r", str(fps),
                "-shortest",
                str(video_only),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if proc.returncode != 0:
                raise PipelineError(
                    f"FFmpeg falhou ao codificar o MP4: {proc.stderr[-800:]}"
                )

            if audio_wav is not None and Path(audio_wav).exists():
                # Muxa vídeo + narração no MP4 final.
                cmd = [
                    ffmpeg, "-y", "-i", str(video_only), "-i", str(audio_wav),
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest",
                    str(output_path),
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if proc.returncode != 0:
                    raise PipelineError(
                        f"FFmpeg falhou ao muxar áudio: {proc.stderr[-800:]}"
                    )
            else:
                # Sem narração: garante um stream de áudio (silêncio) no MP4.
                seconds = max(1.0, float(duration_seconds))
                cmd = [
                    ffmpeg, "-y", "-i", str(video_only),
                    "-f", "lavfi", "-i", f"anullsrc=r=22050:d={seconds}",
                    "-c:v", "copy", "-c:a", "aac", "-shortest",
                    str(output_path),
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if proc.returncode != 0:
                    raise PipelineError(
                        f"FFmpeg falhou ao adicionar áudio: {proc.stderr[-800:]}"
                    )
        except (PipelineError, FileNotFoundError, subprocess.TimeoutExpired, OSError):
            # Falha real na renderização com navegador: propaga (não vira placeholder silencioso).
            raise
        finally:
            # Limpa arquivos temporários (mantém o MP4).
            for f in list(frames_dir.glob("*.png")) + [capture_script, rendered_html]:
                try:
                    f.unlink()
                except OSError:
                    pass
            try:
                frames_dir.rmdir()
            except OSError:
                pass
            if video_only is not None:
                try:
                    video_only.unlink()
                except OSError:
                    pass

        return output_path

    def _capture_script(self, html_path: Path, duration_seconds: float, scenes: list) -> str:
        """Gera o script Node que captura frames do HTML renderizado.

        Avança entre as cenas do storyboard ao longo da duração e grava um
        frame por intervalo de 1/30s em ``FRAMES_DIR`` (variável de ambiente),
        usando caminhos absolutos para não depender do diretório de trabalho.
        """
        scenes_json = json.dumps(scenes or [], ensure_ascii=False)
        return f"""const puppeteer = require(process.env.PUPPETEER_MODULE || 'puppeteer');
(async () => {{
  const executablePath = process.env.CHROME_EXE;
  const framesDir = process.env.FRAMES_DIR;
  if (!executablePath) {{ throw new Error('CHROME_EXE não definido'); }}
  if (!framesDir) {{ throw new Error('FRAMES_DIR não definido'); }}
  const fs = require('fs');
  if (!fs.existsSync(framesDir)) {{ fs.mkdirSync(framesDir, {{ recursive: true }}); }}

  const browser = await puppeteer.launch({{ executablePath, headless: 'new' }});
  const page = await browser.newPage();
  await page.setViewport({{ width: {self.video_width}, height: {self.video_height} }});
  const htmlPath = '{html_path}'.replace(/\\\\/g, '/');
  await page.goto('file:///' + htmlPath, {{ waitUntil: 'networkidle0' }});

  const duration = {float(duration_seconds)};
  const fps = {self.video_fps};
  const total = Math.ceil(duration * fps);
  const scenes = {scenes_json};

  // Limites acumulados de cada cena (segundos).
  const bounds = [];
  let acc = 0;
  for (const s of scenes) {{
    acc += Math.max(0.5, Number(s.seconds || 2));
    bounds.push(acc);
  }}
  const totalDuration = bounds.length ? bounds[bounds.length - 1] : duration;

  let currentSceneIndex = 0;
  for (let i = 0; i < total; i++) {{
    const t = (i / fps);
    const effectiveT = totalDuration > 0 ? Math.min(t, totalDuration) : t;
    // Avança de cena conforme o tempo.
    let sceneIndex = 0;
    for (let b = 0; b < bounds.length; b++) {{
      if (effectiveT > bounds[b]) {{ sceneIndex = b + 1; }} else {{ break; }}
    }}
    if (sceneIndex !== currentSceneIndex && scenes.length) {{
      currentSceneIndex = sceneIndex;
      const scene = scenes[sceneIndex] || {{}};
      await page.evaluate(({{ heading, narration, role }}) => {{
        try {{
          const h = document.querySelector('h1');
          if (h && heading) h.textContent = heading;
          const p = document.querySelector('p.sub');
          if (p && narration && narration.length) p.textContent = narration[0];
          const badge = document.querySelector('.badge');
          if (badge && role) badge.textContent = role.toUpperCase();
          document.title = heading || document.title;
        }} catch (e) {{ /* ignora: template pode não ter todos os elementos */ }}
      }}, scene);
    }}
    await page.screenshot({{
      path: framesDir + '/frame-' + String(i).padStart(4, '0') + '.png',
    }});
  }}
  await browser.close();
}})().catch(e => {{ console.error(e); process.exit(1); }});
"""

    # ------------------------------------------------------------------ #
    # Fallback: MP4 de silêncio/placeholder quando não há navegador      #
    # ------------------------------------------------------------------ #
    def _render_placeholder(self, duration_seconds: float, output_path: Path,
                            ffmpeg: str | None) -> Path:
        if not ffmpeg:
            raise PipelineError(
                "FFmpeg não encontrado e nenhum navegador headless disponível. "
                "Instale o FFmpeg para gerar MP4 de verdade."
            )
        # Vídeo preto com silêncio, duração aproximada. SOMENTE fallback dev.
        seconds = max(1.0, float(duration_seconds))
        cmd = [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", f"color=c=black:s={self.video_width}x{self.video_height}:r={self.video_fps}:d={seconds}",
            "-f", "lavfi", "-i", f"anullsrc=r=22050:d={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(self.video_fps),
            "-c:a", "aac", "-shortest",
            str(output_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise PipelineError(
                f"FFmpeg placeholder falhou: {proc.stderr[-500:]}"
            )
        return output_path


__all__ = ["HTMLRenderer"]
