"""Produção de um rascunho local, sem carregar configuração de publicação."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sysconfig
import tempfile
import textwrap
import wave
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from src.errors import PipelineError
from src.scripts import HOOK_MAX_SECONDS, Script, load_script, read_catalog
from src.settings import ROOT, VideoSettings
from src.video.tts import PiperTTS
from src.video.validator import FFprobeValidator, VideoInfo

_PERFIL = VideoSettings()
_FPS = _PERFIL.fps
_TICKS_POR_QUADRO = 254_016_000_000 // _FPS  # relógio do FilmCraft
_INTERVALO = 6  # 200 ms entre frases da mesma cena
_COMP = "Vídeo vertical"


@dataclass(frozen=True)
class _Fala:
    arquivo: Path
    legenda: str
    inicio: int
    quadros: int


@dataclass(frozen=True)
class _Cena:
    inicio: int
    fim: int
    falas: tuple[_Fala, ...]


def _ler_roteiro(path: Path) -> Script:
    def rejeitar_constante(valor: str) -> None:
        raise ValueError(f"número JSON inválido: {valor}")

    path = path.resolve()
    if path.suffix.lower() != ".json" or path.name.lower().startswith(".env."):
        raise PipelineError("Informe um arquivo de roteiro .json.")
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=rejeitar_constante)
    except (OSError, ValueError) as exc:
        raise PipelineError(f"Não foi possível ler o roteiro JSON: {exc}") from exc
    if isinstance(raw, dict):
        # O snapshot de Script.as_dict contém um total derivado. Recalculamos a duração.
        raw.pop("total_seconds", None)
    catalogo = read_catalog(ROOT / "config" / "sources.yaml")
    return load_script(raw, catalogo, _PERFIL.min_seconds, _PERFIL.max_seconds)


def _binario(nome: str, opcao: str, explicito: str | None = None, *extras: Path) -> str:
    for candidato in ([explicito] if explicito else [nome, *map(str, extras)]):
        encontrado = shutil.which(candidato)
        if encontrado:
            return encontrado
    dica = f" Use {opcao} para indicar o executável." if opcao else " Adicione-o ao PATH."
    raise PipelineError(f"Executável não encontrado: {explicito or nome}.{dica}")


def _artcraft(app: str, explicito: str | None) -> str:
    nome = f"{app.lower()}-cli"
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    return _binario(nome, f"--{app.lower()}-bin", explicito,
                    program_files / "ArtCraft" / app / f"{nome}.exe",
                    program_files / app / f"{nome}.exe")


def _duracao_wav(path: Path) -> int:
    """Mede voz PCM e completa a última fração de frame para o editor não cortar a fala."""
    try:
        with wave.open(str(path), "rb") as wav:
            params = wav.getparams()
            pcm = wav.readframes(params.nframes)
        if params.nchannels != 1 or params.sampwidth != 2 or params.framerate % _FPS:
            raise PipelineError("Use um modelo Piper mono PCM 16 bits com taxa divisível por 30.")
        amostras = np.frombuffer(pcm, dtype="<i2")
        if amostras.size != params.nframes or not np.any((amostras > 164) | (amostras < -164)):
            raise PipelineError(f"Frase silenciosa ou incompleta: {path.name}.")
        por_quadro = params.framerate // _FPS
        quadros = (params.nframes + por_quadro - 1) // por_quadro
        falta = quadros * por_quadro - params.nframes
        if falta:
            with wave.open(str(path), "wb") as wav:
                wav.setparams(params)
                wav.writeframes(pcm + bytes(2 * falta))
        return quadros
    except (wave.Error, EOFError, ValueError) as exc:
        raise PipelineError(f"WAV inválido: {path.name}.") from exc


def _planejar(script: Script, tts: PiperTTS) -> list[_Cena]:
    inicio = 0
    plano = []
    for indice, cena in enumerate(script.scenes, 1):
        frases = [" ".join(frase.split()) for frase in cena.narration]
        linhas = [textwrap.wrap(frase, width=36, break_on_hyphens=False) for frase in frases]
        if any(len(item) > 2 for item in linhas):
            raise PipelineError(f"Divida a narração da cena {indice} em frases que caibam em duas linhas de legenda.")
        cursor = inicio
        falas = []
        for segmento, legenda in zip(tts.synthesize_scene(cena.role, frases, indice), linhas, strict=True):
            quadros = _duracao_wav(segmento.wav_path)
            falas.append(_Fala(segmento.wav_path, "\n".join(legenda), cursor, quadros))
            cursor += quadros + _INTERVALO
        fim = max(inicio + math.ceil(cena.seconds * _FPS), cursor - _INTERVALO)
        if cena.role == "gancho" and fim - inicio > HOOK_MAX_SECONDS * _FPS:
            raise PipelineError(f"A voz do gancho excede {HOOK_MAX_SECONDS}s; encurte a narração.")
        if fim > _PERFIL.max_seconds * _FPS:
            raise PipelineError(f"A duração com narração excede {_PERFIL.max_seconds}s; encurte o roteiro.")
        plano.append(_Cena(inicio, fim, tuple(falas)))
        inicio = fim
    return plano


def _rodar(argv: list[str], pasta: Path, etapa: str, *, dados: bytes | None = None,
           timeout: int = 120) -> bytes:
    try:
        resultado = subprocess.run(argv, input=dados, cwd=pasta, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(f"{etapa} excedeu o tempo limite ({timeout}s).") from exc
    except OSError as exc:
        raise PipelineError(f"Não foi possível iniciar {etapa}: {exc}") from exc
    if resultado.returncode:
        detalhe = (resultado.stderr or resultado.stdout).decode("utf-8", "replace").strip()[-900:]
        raise PipelineError(f"{etapa} falhou (código {resultado.returncode}): {detalhe}")
    return resultado.stdout


def _arquivo_criado(path: Path) -> None:
    if not path.is_file() or not path.stat().st_size:
        raise PipelineError(f"A ferramenta não gravou {path.name}.")


def _passo(id_: str, **params) -> dict:
    return {"id": id_, "params": params}


def _compor(script: Script, plano: list[_Cena], pasta: Path, cli: str) -> Path:
    # Layout vertical com margem para os controles da plataforma e área de legenda livre.
    paletas = (
        ("#18312D", "#D7F789", "#345C4D"),
        ("#24264E", "#B4C7FF", "#454A83"),
        ("#402839", "#FFD08D", "#764763"),
    )
    passos = [_passo("comp.new", name=_COMP, width=_PERFIL.width, height=_PERFIL.height,
                     frameRate=_FPS, duration=plano[-1].fim / _FPS, background=paletas[0][0])]
    for i, (cena, corte) in enumerate(zip(script.scenes, plano, strict=True)):
        fundo, destaque, contraste = paletas[i % len(paletas)]
        inicio, fim = corte.inicio / _FPS, corte.fim / _FPS

        def camada(id_: str, nome: str, **params) -> str:
            nome = f"{i + 1:02d} · {nome}"
            passos.append(_passo(id_, name=nome, **params))
            passos.append(_passo("layer.timing", layers=[nome], **{"in": inicio, "out": fim}))
            return nome

        camada("layer.newSolid", "Fundo", color=fundo, width=_PERFIL.width, height=_PERFIL.height)
        tema = "FICÇÃO" if script.kind == "ficcao" else script.template.upper()
        camada("layer.newText", "Série", text=f"{tema}   /   {i + 1:02d}",
               box=[94, 167, 820, 66], font="Consolas", style="Bold", size=34, fill=destaque)
        camada("layer.newShape", "Filete", kind="rect", position=[506, 254], size=[824, 4], fill=destaque)
        titulo = "\n".join(textwrap.wrap(" ".join(cena.heading.split()), width=17, break_on_hyphens=False))
        camada("layer.newText", "Título", text=titulo, box=[92, 333, 832, 520], font="Segoe UI",
               style="Bold", size=82, leading=94, fill="#F8F4E7")
        centro = [505, 1070] if i % 2 == 0 else [505, 1030]
        camada("layer.newShape", "Painel", kind="rect", position=centro, size=[824, 410], fill=contraste)
        # Motivo gráfico alternado e animação determinística no relógio da composição.
        x, y = (325, centro[1]) if i % 2 == 0 else (665, centro[1])
        motivo = camada("layer.newShape", "Sinal", kind="ellipse", position=[x, y],
                        size=[260, 260], fill=destaque)
        alvo = 655 if i % 2 == 0 else 335
        for t, pos in ((inicio, [x, y]), (max(inicio, fim - 1 / _FPS), [alvo, y])):
            passos.append(_passo("prop.addKey", layer=motivo, path="transform/position", value=pos,
                                 time=t, timeBase="comp"))

    projeto = pasta / "composicao.ecproj"
    bruto = _rodar([cli, "--empty", "--json", "--save-as", str(projeto), "run", "--commands", "-"],
                   pasta, "composição EffectCraft", dados=json.dumps(passos).encode("utf-8"))
    try:
        respostas = json.loads(bruto)
        if isinstance(respostas, dict):
            # Com --save-as, o EffectCraft envolve os passos junto ao caminho salvo.
            respostas = respostas.get("result")
        if not isinstance(respostas, list) or len(respostas) != len(passos) or any(
            not isinstance(r, dict) or "error" in r or r.get("command") != passo["id"]
            for r, passo in zip(respostas, passos)
        ):
            raise ValueError
    except ValueError as exc:
        raise PipelineError("O EffectCraft não confirmou os comandos da composição.") from exc
    _arquivo_criado(projeto)
    imagem = pasta / "imagem-effectcraft.mp4"
    _rodar([cli, "--project", str(projeto), "render", "--comp", _COMP, "--out", str(imagem),
            "--format", "h264", "--bitrate", "4200", "--audio", "off"], pasta,
           "render EffectCraft", timeout=_PERFIL.render_timeout_seconds)
    _arquivo_criado(imagem)
    return imagem


def _filmcraft(cli: str, pasta: Path, passos: list[dict], *args: str, timeout: int = 120) -> list:
    dados = "".join(json.dumps(passo) + "\n" for passo in passos).encode("utf-8")
    bruto = _rodar([cli, "--compact", "--data-dir", str(pasta / "filmcraft-local"), *args, "run", "-"],
                   pasta, f"FilmCraft ({passos[0]['id']})", dados=dados, timeout=timeout)
    try:
        respostas = [json.loads(linha) for linha in bruto.splitlines() if linha.strip()]
        if len(respostas) != len(passos) or any(
            not isinstance(r, dict) or r.get("ok") is not True or "result" not in r for r in respostas
        ):
            raise ValueError
    except ValueError as exc:
        raise PipelineError(f"O FilmCraft não confirmou a operação {passos[0]['id']}.") from exc
    return [r["result"] for r in respostas]


def _montar(script: Script, plano: list[_Cena], imagem: Path, pasta: Path, cli: str, mp4: Path) -> None:
    falas = [fala for corte in plano for fala in corte.falas]
    fontes = pasta / "fontes.fcproj"
    paths = [str(imagem), *(str(fala.arquivo) for fala in falas)]
    importado, = _filmcraft(cli, pasta, [_passo("file.import", paths=paths)], "--save-as", str(fontes))
    itens = importado.get("items") if isinstance(importado, dict) else None
    if (not isinstance(itens, list) or len(itens) != len(paths)
        or not all(type(item) is int for item in itens) or len(set(itens)) != len(itens)
        or importado.get("errors")):
        raise PipelineError("O FilmCraft não importou todas as mídias.")
    _arquivo_criado(fontes)
    video, *vozes = itens
    projeto = pasta / "montagem.fcproj"
    srt = pasta / "legendas.pt-BR.srt"
    passos = [
        _passo("file.newSequence", name=script.title, width=_PERFIL.width, height=_PERFIL.height,
               fps=_FPS, sampleRate=48000, video=1, audio=1),
        _passo("timeline.place", item=video, track="V1", frame=0, video=True, audio=False),
        *(_passo("timeline.razor", time=corte.inicio * _TICKS_POR_QUADRO, track="V1") for corte in plano[1:]),
        *(_passo("timeline.place", item=item, track="A1", frame=fala.inicio, video=False, audio=True)
          for item, fala in zip(vozes, falas, strict=True)),
        _passo("mixer.setStrip", strip="A1", name="Voz · Piper", volumeDb=-3.0),
        _passo("captions.newTrack", name="Português (Brasil)", language="pt-BR", format="Subtitle"),
        _passo("captions.setStyle", track="C1", font="Segoe UI", fontStyle="SemiBold", size=49,
               color="#F8F4E7", background=True, backgroundColor="#18232BF0", align="center",
               anchor="bottom", margin=0.196, lineSpacing=1.14),
        *(_passo("captions.add", track="C1", text=fala.legenda, time=fala.inicio * _TICKS_POR_QUADRO,
                 durationSeconds=fala.quadros / _FPS) for fala in falas),
        _passo("captions.export", track="C1", path=str(srt), format="srt"),
    ]
    _filmcraft(cli, pasta, passos, "--project", str(fontes), "--save-as", str(projeto))
    _arquivo_criado(projeto)
    _arquivo_criado(srt)
    resultado, = _filmcraft(cli, pasta, [_passo(
        "file.exportMedia", path=str(mp4), format="h264", audioCodec="aac", h264Profile="high",
        bitrateKbps=2600, bitrateMode="vbr1Pass", audio=True, burnCaptions=True, range="entire", wait=True,
    )], "--project", str(projeto), timeout=_PERFIL.render_timeout_seconds)
    if not isinstance(resultado, dict) or resultado.get("offlineMedia"):
        raise PipelineError("O FilmCraft não confirmou a exportação com todas as mídias online no projeto.")
    _arquivo_criado(mp4)


def produzir_local(roteiro: Path, *, saida: Path, piper_model: Path,
                   piper_bin: str | None = None, effectcraft_bin: str | None = None,
                   filmcraft_bin: str | None = None) -> VideoInfo:
    """Gera MP4 vertical, projetos editáveis e SRT; entrega o MP4 só após os gates."""
    script = _ler_roteiro(roteiro)
    piper_model = piper_model.resolve()
    if piper_model.suffix.lower() != ".onnx" or not piper_model.is_file() or not Path(f"{piper_model}.json").is_file():
        raise PipelineError(f"Modelo Piper e seu arquivo .onnx.json são obrigatórios: {piper_model}")
    executavel = Path(sysconfig.get_path("scripts", sysconfig.get_preferred_scheme("user"))) / "piper"
    piper = _binario("piper", "--piper-bin", piper_bin, executavel.with_suffix(".exe") if os.name == "nt" else executavel)
    effectcraft = _artcraft("EffectCraft", effectcraft_bin)
    filmcraft = _artcraft("FilmCraft", filmcraft_bin)
    ffmpeg = _binario(_PERFIL.ffmpeg, "")
    ffprobe = _binario(_PERFIL.ffprobe, "")

    saida = saida.resolve()
    saida.mkdir(parents=True, exist_ok=True)
    pasta = Path(tempfile.mkdtemp(prefix="rascunho-", dir=saida))
    try:
        tts = PiperTTS(piper_bin=piper, model_path=piper_model, output_dir=pasta / "vozes", require_real=True)
        plano = _planejar(script, tts)
        imagem = _compor(script, plano, pasta, effectcraft)
        with tempfile.TemporaryDirectory(prefix=".validacao-", dir=pasta) as temporario:
            mp4 = Path(temporario) / "exportacao.mp4"
            _montar(script, plano, imagem, pasta, filmcraft, mp4)
            validador = FFprobeValidator(ffprobe_bin=ffprobe, ffmpeg_bin=ffmpeg,
                                        require_quality_measurements=True)
            info = validador.validate_tiktok(mp4, width=_PERFIL.width, height=_PERFIL.height, fps=_FPS,
                                            max_duration_seconds=_PERFIL.max_seconds, strict=True)
            if info.codec != "h264" or "mp4" not in info.format_name.split(",") or info.fps != _FPS:
                raise PipelineError("O export precisa ser MP4/H.264 a 30 fps.")
            if not (_PERFIL.min_seconds <= info.duration_seconds <= _PERFIL.max_seconds):
                raise PipelineError(f"Duração fora do intervalo do perfil: {info.duration_seconds:.2f}s.")
            if abs(info.duration_seconds - plano[-1].fim / _FPS) > 1 / _FPS + 0.001:
                raise PipelineError("Duração do MP4 não corresponde à montagem.")
            final = mp4.replace(pasta / "video.mp4")
            return replace(info, path=final)
    except (PipelineError, OSError, wave.Error, EOFError) as exc:
        raise PipelineError(f"{exc} (intermediários: {pasta})") from exc
