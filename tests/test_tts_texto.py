"""Regressão: o texto falado não é a legenda nem os metadados da postagem."""

import subprocess
import wave

import pytest

from src.errors import PipelineError
from src.video.tts import PiperTTS


@pytest.fixture
def piper(tmp_path, monkeypatch):
    modelo = tmp_path / "voz.onnx"
    modelo.touch()
    enviados = []

    def sintetizar(argv, *, input, **kwargs):
        enviados.append(input.decode("utf-8"))
        with wave.open(argv[argv.index("--output_file") + 1], "wb") as wav:
            wav.setparams((1, 2, 3000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x10\x02" * 3000)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", sintetizar)
    return PiperTTS(model_path=modelo, output_dir=tmp_path, require_real=True), enviados


@pytest.mark.parametrize(("frase", "fala"), [
    ("Ouça **DevExMachine** ©®™. #história 🤖", "Ouça DevExMachine."),
    ("Conheça @DevExMachine e veja [o farol](https://exemplo.test/luz).", "Conheça DevExMachine e veja o farol."),
    ("A luz cresceu 30% e chegou a 40 °C.", "A luz cresceu 30% e chegou a 40 °C."),
    ("Diga arroba. O projeto é em C# e C++.", "Diga arroba. O projeto é em C# e C++."),
    ("Leia: https://exemplo.test/luz. Vamos subir!", "Leia. Vamos subir!"),
    ("[Abertura] Ninguém acreditou. #1", "[Abertura] Ninguém acreditou. 1"),
])
def test_limpa_apenas_o_texto_enviado_ao_piper(piper, frase, fala):
    tts, enviados = piper
    segmento, = tts.synthesize_scene("corpo", [frase], 1)
    assert enviados == [fala]
    assert segmento.text == frase
    assert segmento.duration_seconds == 1


def test_marcadores_sem_fala_nao_geram_wav_nem_aprovacao(piper):
    tts, enviados = piper
    with pytest.raises(PipelineError, match="sem texto falável"):
        tts.synthesize_scene("gancho", ["Luz no céu", "© #Farol 🤖"], 1)
    assert enviados == []
