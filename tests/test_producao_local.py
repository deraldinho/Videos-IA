"""O comando local isola a fila e integra as CLIs sem subprocessos reais."""

import json
import struct
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

import main


@pytest.fixture
def roteiro(tmp_path):
    path = tmp_path / "roteiro.json"
    path.write_text(json.dumps({
        "topic": "Farol imaginário",
        "title": "O farol das nuvens",
        "template": "historia",
        "kind": "ficcao",
        "description": "Uma pequena história fictícia.",
        "hashtags": ["#Ficcao", "#Historia", "#Inventado"],
        "scenes": [
            {"role": "gancho", "seconds": 3, "heading": "O farol acendeu",
             "narration": ["O farol acendeu."]},
            {"role": "corpo", "seconds": 20, "heading": "A luz subiu",
             "narration": ["A luz subiu.", "Ninguém sabia o motivo."]},
            {"role": "cta", "seconds": 8, "heading": "O próximo amanhecer",
             "narration": ["Até amanhã."]},
        ],
    }), encoding="utf-8")
    (tmp_path / "voz.onnx").write_bytes(b"modelo simulado")
    (tmp_path / "voz.onnx.json").write_text("{}", encoding="utf-8")
    return path


@pytest.fixture
def ferramentas(monkeypatch):
    def proibido(*args, **kwargs):
        pytest.fail("A produção local não pode abrir o orquestrador nem carregar .env")

    monkeypatch.setattr(main, "Orchestrator", proibido)
    monkeypatch.setattr("src.settings.load_dotenv", proibido)
    monkeypatch.setattr("shutil.which", lambda name, **_: name)
    estado = {"chamadas": [], "montagem": [], "audio": True, "amplitude": 1000,
              "amostras": 3001, "frames": 0, "falha_video": False}

    def executar(argv, *, input=None, **kwargs):
        app = Path(argv[0]).stem
        estado["chamadas"].append(app)
        assert kwargs.get("shell") is not True
        out = b""
        err = ""
        if app == "piper":
            assert input
            with wave.open(argv[argv.index("--output_file") + 1], "wb") as wav:
                wav.setparams((1, 2, 3000, 0, "NONE", "not compressed"))
                wav.writeframes(struct.pack("<h", estado["amplitude"]) * estado["amostras"])
        elif app == "effectcraft-cli":
            if "--save-as" in argv:
                Path(argv[argv.index("--save-as") + 1]).write_bytes(b"composicao")
                steps = json.loads(input)
                assert steps[0]["params"]["duration"] == 31
                out = json.dumps({"result": [{"command": s["id"], "result": None} for s in steps],
                                  "saved": argv[argv.index("--save-as") + 1]}).encode()
            else:
                assert "render" in argv
                Path(argv[argv.index("--out") + 1]).write_bytes(b"effectcraft")
        elif app == "filmcraft-cli":
            resultados = []
            for step in map(json.loads, input.splitlines()):
                params = step["params"]
                cmd = step["id"]
                result = None
                if cmd == "file.import":
                    assert all(Path(p).is_file() for p in params["paths"])
                    result = {"items": list(range(1, len(params["paths"]) + 1)), "errors": []}
                else:
                    estado["montagem"].append(step)
                if cmd == "captions.export":
                    Path(params["path"]).write_text("1\n00:00:00,000 --> 00:00:01,033\nO farol acendeu.\n", encoding="utf-8")
                if cmd == "file.exportMedia":
                    assert params["wait"] and params["burnCaptions"]
                    Path(params["path"]).write_bytes(b"exportacao final")
                    result = {"offlineMedia": []}
                resultados.append({"ok": True, "result": result})
            if "--save-as" in argv:
                Path(argv[argv.index("--save-as") + 1]).write_bytes(b"sequencia")
            out = "\n".join(map(json.dumps, resultados)).encode()
        elif app == "ffprobe":
            streams = [{"codec_type": "video", "width": 1080, "height": 1920,
                        "r_frame_rate": "30/1", "codec_name": "h264"}]
            if estado["audio"]:
                streams.append({"codec_type": "audio", "codec_name": "aac"})
            out = json.dumps({"streams": streams, "format": {
                "format_name": "mov,mp4", "duration": "31.0", "size": "1024"}}).encode()
        elif app == "ffmpeg":
            if "volumedetect" in argv:
                err = "mean_volume: -23.0 dB"
            elif "rawvideo" in argv:
                if estado["falha_video"]:
                    return subprocess.CompletedProcess(argv, 0, b"", b"")
                frame = np.zeros((160, 160), dtype=np.uint8)
                frame[:, :80] = 150
                if estado["frames"] % 2:
                    frame = frame.T
                estado["frames"] += 1
                out = frame.tobytes()
        else:
            pytest.fail(f"Subprocesso inesperado: {app}")
        if kwargs.get("text"):
            return subprocess.CompletedProcess(argv, 0, out.decode(), err)
        return subprocess.CompletedProcess(argv, 0, out, err.encode())

    monkeypatch.setattr(subprocess, "run", executar)
    return estado


def argumentos(roteiro):
    return ["produzir-local", str(roteiro), "--saida", str(roteiro.parent / "jobs"),
            "--piper-model", str(roteiro.parent / "voz.onnx")]


def test_cli_monta_legenda_e_exporta_em_diretorio_novo(roteiro, ferramentas, capsys):
    saida = roteiro.parent / "jobs"
    saida.mkdir()
    anterior = saida / "video.mp4"
    anterior.write_bytes(b"original")
    assert main.main(argumentos(roteiro)) == 0
    assert anterior.read_bytes() == b"original"
    pasta, = [p for p in saida.iterdir() if p.is_dir()]
    assert (pasta / "video.mp4").read_bytes() == b"exportacao final"
    assert (pasta / "montagem.fcproj").is_file()
    assert (pasta / "composicao.ecproj").is_file()
    assert (pasta / "legendas.pt-BR.srt").is_file()
    assert not list(pasta.glob(".validacao-*"))
    assert ferramentas["chamadas"].count("piper") == 4
    montagem = ferramentas["montagem"]
    vozes = [s["params"] for s in montagem if s["id"] == "timeline.place" and s["params"]["audio"]]
    legendas = [s["params"] for s in montagem if s["id"] == "captions.add"]
    assert [v["frame"] for v in vozes] == [0, 90, 127, 690]
    assert [l["time"] for l in legendas] == [v["frame"] * 8_467_200_000 for v in vozes]
    assert all(l["durationSeconds"] == pytest.approx(31 / 30) for l in legendas)
    assert "validado para revisão" in capsys.readouterr().out


@pytest.mark.parametrize(("estado", "mensagem"), [
    ({"audio": False}, "sem stream de áudio"),
    ({"falha_video": True}, "variação visual"),
    ({"amplitude": 0}, "silenciosa"),
    ({"amostras": 9001}, "gancho"),
])
def test_cli_nunca_entrega_mp4_sem_gate(roteiro, ferramentas, capsys, estado, mensagem):
    ferramentas.update(estado)
    assert main.main(argumentos(roteiro)) == 1
    assert mensagem in capsys.readouterr().err
    assert not list((roteiro.parent / "jobs").rglob("video.mp4"))
    if "amostras" in estado or "amplitude" in estado:
        assert "effectcraft-cli" not in ferramentas["chamadas"]


def test_cli_rejeita_roteiro_invalido_antes_de_abrir_ferramentas(roteiro, ferramentas, capsys):
    roteiro.write_text('{"scenes": NaN}', encoding="utf-8")
    assert main.main(argumentos(roteiro)) == 1
    assert "roteiro" in capsys.readouterr().err
    assert ferramentas["chamadas"] == []
    assert not (roteiro.parent / "jobs").exists()
