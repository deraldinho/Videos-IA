"""Testes de regressão CR-10: versionar módulos, executar regressão, configurar CI.

Defeito: não havia versão nos módulos Python (src/__init__ sem __version__),
não havia metadata de build (setup/pyproject) e não havia CI configurada
(GitHub Actions) reproduzível — impedindo rastreabilidade de builds e Quality
Gate remoto.

Contratos esperados:
- src.__version__ existe e segue SemVer.
- pyproject.toml existe com versão coerente com src.__version__.
- .github/workflows/ci.yml existe e executa: instalação reprodutível, validação
  de sintaxe/importação, suíte pytest isolada (sem APIs externas/reais).
- Nenhuma das operações de CI utiliza contas reais de TikTok/Buffer/Cloudinary.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# CR-10: versionamento de módulos                                             #
# --------------------------------------------------------------------------- #
def test_src_tem_versao_semver():
    """src.expõe __version__ no formato SemVer MAJOR.MINOR.PATCH."""
    import src

    versao = src.__version__
    partes = versao.split(".")
    assert len(partes) == 3
    assert all(p.isdigit() for p in partes), f"versão não é SemVer: {versao}"


def test_pyproject_tem_versao_coerente_com_src():
    """pyproject.toml declara a mesma versão de src.__version__."""
    import tomllib

    import src

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert pyproject.exists(), "pyproject.toml ausente"
    dados = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert dados["project"]["version"] == src.__version__


# --------------------------------------------------------------------------- #
# CR-10: CI configurada                                                       #
# --------------------------------------------------------------------------- #
def test_ci_github_actions_existe():
    """GitHub Actions CI existe em .github/workflows/ci.yml."""
    ci = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
    assert ci.exists(), "CI do GitHub Actions ausente"


def test_ci_nao_utiliza_contas_reais():
    """A CI não faz upload/agendamento real em TikTok/Buffer/Cloudinary."""
    ci = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
    texto = ci.read_text(encoding="utf-8")
    proibidos = ["tiktok-upload", "buffer publish", "cloudinary upload real"]
    for p in proibidos:
        assert p not in texto.lower(), f"CI usa operação real proibida: {p}"


# --------------------------------------------------------------------------- #
# CR-10: regressão aplicável (suíte isolada)                                  #
# --------------------------------------------------------------------------- #
def test_suite_isolada_pode_ser_executada_sem_api_externa(monkeypatch):
    """A suíte de regressão não depende de APIs externas nem credenciais."""
    # Garante que nenhuma variável de credencial real vaze para os testes.
    for var in ("TIKTOK_ACCESS_TOKEN", "CLOUDINARY_API_SECRET", "BUFFER_ACCESS_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    import src

    # Importação dos módulos principais deve funcionar sem credenciais.
    import src.database  # noqa: F401
    import src.graph_orchestrator  # noqa: F401
    import src.publication_policy  # noqa: F401
    import src.video.hyperframes_adapter  # noqa: F401

    assert src.__version__


__all__ = []
