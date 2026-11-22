"""Testes para os provedores de LLM (AssistedProvider e NVIDIAProvider)."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

import pytest

from src.ai.provider import AssistedProvider, NVIDIAProvider, build_provider
from src.errors import PipelineError


# --------------------------------------------------------------------------- #
# AssistedProvider                                                            #
# --------------------------------------------------------------------------- #
def test_assisted_provider_writes_prompt_and_reads_response():
    with tempfile.TemporaryDirectory() as td:
        inbox = Path(td)
        provider = AssistedProvider(inbox)
        prompt = "Prompt de teste para o roteiro."

        # Primeira chamada: grava o prompt e levanta PipelineError (não há response).
        with pytest.raises(PipelineError, match="FCC-Claude"):
            provider.complete(prompt)

        # O prompt foi gravado com nome derivado do prompt (determinístico).
        prompt_files = list(inbox.glob("*.prompt.txt"))
        assert len(prompt_files) == 1
        assert prompt in prompt_files[0].read_text(encoding="utf-8")

        # Grava o response com o mesmo run_id (nome base do prompt, sem .prompt.txt).
        run_id = prompt_files[0].name.removesuffix(".prompt.txt")
        response_file = inbox / f"{run_id}.response.json"
        response_file.write_text(
            json.dumps({"topic": "teste", "template": "curiosidade", "kind": "factual"}),
            encoding="utf-8",
        )

        # Segunda chamada com o mesmo prompt: lê o response e retorna.
        result = provider.complete(prompt)
        assert result["topic"] == "teste"
        assert result["template"] == "curiosidade"


def test_assisted_provider_rejects_invalid_json_response():
    with tempfile.TemporaryDirectory() as td:
        inbox = Path(td)
        provider = AssistedProvider(inbox)
        prompt = "qualquer coisa"

        # Primeira chamada: grava o prompt e falha (sem response).
        try:
            provider.complete(prompt)
        except PipelineError:
            pass

        prompt_file = list(inbox.glob("*.prompt.txt"))[0]
        run_id = prompt_file.name.removesuffix(".prompt.txt")
        response_file = inbox / f"{run_id}.response.json"
        response_file.write_text("não é json", encoding="utf-8")

        # Segunda chamada: tenta ler o response inválido.
        with pytest.raises(PipelineError, match="JSON de resposta inválido"):
            provider.complete(prompt)


# --------------------------------------------------------------------------- #
# NVIDIAProvider                                                              #
# --------------------------------------------------------------------------- #
def test_nvidia_provider_requires_api_key():
    with pytest.raises(PipelineError, match="NVIDIA_API_KEY"):
        NVIDIAProvider(api_key="", model="qualquer")


def test_build_provider_assisted_defaults():
    provider = build_provider("assisted")
    assert isinstance(provider, AssistedProvider)


def test_build_provider_unknown():
    with pytest.raises(PipelineError, match="desconhecido"):
        build_provider("provedor_inexistente")


__all__ = []
