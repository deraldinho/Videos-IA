"""Provedores de LLM: modo ``assisted`` (arquivo para revisão humana) e NVIDIA NIM.

O modo ``assisted`` não faz nenhuma chamada de rede: escreve o prompt em
``data/inbox/`` para o operador entregar via FCC-Claude e coleta o JSON de
resposta. O modo ``nvidia_nim`` chama a API NVIDIA NIM apenas quando o usuário
autorizou explicitamente (``allow_nvidia_requests: true`` + chave válida).
"""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

import requests

from src.errors import PipelineError
from src.ideas import LLMProvider

# Bloco JSON extraído de um texto que pode conter comentário ao redor.
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    """Extrai o primeiro objeto JSON válido de um texto livre."""
    text = text.strip()
    # Remove cercas de código tipo ```json ... ```
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE | re.MULTILINE)
    text = re.sub(r"\s*```\s*$", "", text)
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        raise PipelineError("O modelo não retornou um objeto JSON válido.")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise PipelineError(f"Resposta do modelo não é JSON válido: {exc}") from exc


class AssistedProvider(LLMProvider):
    """Modo assistido: salva o prompt para o operador revisar via FCC-Claude.

    Não faz requisições de rede. O fluxo esperado é:
    1. ``complete`` escreve o prompt em ``data/inbox/<id>.prompt.txt``.
    2. O operador submete ao FCC-Claude e devolve o JSON em ``data/inbox/<id>.response.json``.
    3. ``complete`` lê e retorna esse JSON (ou levanta PipelineError se ainda não houver).
    """

    def __init__(self, inbox: Path | str) -> None:
        self.inbox = Path(inbox)
        self.inbox.mkdir(parents=True, exist_ok=True)

    def complete(
        self, prompt: str, run_id: str | None = None
    ) -> dict:  # pragma: no cover - depende do operador
        """Escreve o prompt para o operador e devolve o JSON de resposta.

        Se ``run_id`` não for informado, deriva um ID determinístico do prompt
        (SHA-256), de modo que o mesmo prompt sempre reusa os mesmos arquivos
        de prompt/response — essencial para o fluxo assisted (gravar prompt,
        aguardar resposta do operador, ler na próxima chamada).
        """
        if run_id is None:
            run_id = sha256(prompt.encode("utf-8")).hexdigest()[:16]
        prompt_path = self.inbox / f"{run_id}.prompt.txt"
        response_path = self.inbox / f"{run_id}.response.json"

        prompt_path.write_text(prompt, encoding="utf-8")

        if not response_path.exists():
            raise PipelineError(
                f"Modo assisted: prompt gravado em {prompt_path}. "
                "Submeta ao FCC-Claude e devolva o JSON em "
                f"{response_path} antes de continuar."
            )

        try:
            return json.loads(response_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PipelineError(f"JSON de resposta inválido em {response_path}: {exc}") from exc


class NVIDIAProvider(LLMProvider):
    """Chama a API NVIDIA NIM (somente com chave autorizada pelo usuário)."""

    def __init__(self, api_key: str, model: str, base_url: str | None = None) -> None:
        if not api_key:
            raise PipelineError("NVIDIA_API_KEY é obrigatória para o provedor nvidia_nim.")
        self.api_key = api_key
        self.model = model or "nvidia/nemotron-3.5-super-vl-120b-a12b"
        self.base_url = base_url or "https://integrate.api.nvidia.com/v1"

    def complete(self, prompt: str) -> dict:
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 4096,
                },
                timeout=180,
            )
        except requests.RequestException as exc:  # pragma: no cover - rede externa
            raise PipelineError(f"Falha de rede na NVIDIA NIM: {exc}") from exc

        if resp.status_code >= 400:
            raise PipelineError(
                f"NVIDIA NIM retornou HTTP {resp.status_code}: {resp.text[:500]}"
            )

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise PipelineError(f"Resposta inválida da NVIDIA NIM: {exc}") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise PipelineError(
                f"Estrutura inesperada na resposta da NVIDIA NIM: {data}"
            ) from exc

        return _extract_json(content)


def build_provider(provider: str, *, api_key: str | None = None,
                   model: str | None = None, inbox: Path | str | None = None) -> LLMProvider:
    """Factory: constrói o provedor de acordo com o nome."""
    name = (provider or "assisted").lower()
    if name == "assisted":
        if inbox is None:
            inbox = Path(__file__).resolve().parents[2] / "data" / "inbox"
        return AssistedProvider(inbox)
    if name == "nvidia_nim":
        return NVIDIAProvider(api_key=api_key or "", model=model or "")
    raise PipelineError(f"Provedor de LLM desconhecido: {provider!r} (use 'assisted' ou 'nvidia_nim')")


__all__ = ["AssistedProvider", "NVIDIAProvider", "build_provider"]
