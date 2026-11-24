"""Testes de regressão CR-09: reconciliação de worktrees e fix do submodule hyperframes.

Defeito: o diretório ``vendor/hyperframes`` (submodule do repositório HeyGen/HyperFrames)
está corrompido — seu nome do sistema de arquivos contém injeção de shell
(``vendor/hyperframes" 2>&1 | tail -5; ls d:...``), impedindo o Git de reconhecê-lo
como submodule válido e quebrando a resolução do caminho pelo
``HyperFramesAdapter``.

Contratos esperados:
- O ``HyperFramesAdapter`` não quebra quando ``vendor/hyperframes`` está ausente
  ou corrompido: cai em fallback para ``HTMLRenderer`` (``is_available == False``).
- O caminho padrão do vendor aponta para ``vendor/hyperframes`` (sem hardcode de
  ambiente/worktree).
- Não há dependência direta do pacote Python ``hyperframes`` (HTTP/2).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.video.hyperframes_adapter import HyperFramesAdapter


# --------------------------------------------------------------------------- #
# CR-09: submodule hyperframes / worktrees                                    #
# --------------------------------------------------------------------------- #
def test_hyperframes_adapter_caminho_padrao_vendor():
    """Caminho padrão do vendor é vendor/hyperframes (raiz do projeto)."""
    adapter = HyperFramesAdapter()
    esperado = Path(__file__).resolve().parents[1] / "vendor" / "hyperframes"
    assert adapter.vendor_dir == esperado


def test_hyperframes_adapter_nao_quebra_quando_vendor_ausente(tmp_path):
    """Quando vendor/hyperframes nao existe, is_available e False e cai no fallback."""
    vendor_dir = tmp_path / "vendor" / "hyperframes"
    adapter = HyperFramesAdapter(vendor_dir=vendor_dir)
    assert adapter.is_available is False
    # render deve funcionar via fallback (HTMLRenderer) mesmo sem hyperframes.
    # Nao executamos render completo (depende de ffmpeg/chromium); apenas
    # verificamos que o adapter se constroi e reporta indisponibilidade.


def test_nao_depende_do_pacote_python_hyperframes():
    """Não há import do pacote Python 'hyperframes' (HTTP/2, nao relacionado)."""
    import src.video.hyperframes_adapter as mod
    fonte = Path(mod.__file__).read_text(encoding="utf-8")
    assert "import hyperframes" not in fonte
    assert "from hyperframes" not in fonte


__all__ = []
