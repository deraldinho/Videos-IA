"""Testes de regressão CR-08: isolamento de persistência/checkpoints por ambiente.

Defeito: ``GraphOrchestrator`` e ``Database`` sempre gravam nos mesmos caminhos
fixos (``data/videos.sqlite3`` e ``data/langgraph_checkpoints.sqlite3``),
independente do ambiente (dev/test/prod). Não há como isolar os dados de cada
ambiente, o que pode misturar vídeos de testes com dados reais e reutilizar
checkpoints do LangGraph entre execuções de ambientes distintos.

Contratos esperados:
- ``Database`` aceita um ``db_path`` informado e nele cria/todas as tabelas.
- ``GraphOrchestrator`` aceita ``db_path`` e o repassa ao ``Database``.
- Cada instância com caminho distinto opera em banco isolado (sem vazar dados
  entre ambientes).
- O checkpointer do LangGraph pode ser direcionado a um caminho por ambiente.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.database import Database, DEFAULT_DB_PATH
from src.graph_orchestrator import GraphOrchestrator


# --------------------------------------------------------------------------- #
# CR-08: isolamento por ambiente                                              #
# --------------------------------------------------------------------------- #
def test_database_aceita_caminho_isolado_por_ambiente(tmp_path):
    """Database aceita db_path customizado e cria o schema nele."""
    db_path = tmp_path / "ambiente-test" / "videos.sqlite3"
    db = Database(db_path)
    try:
        row = db.create_video(
            video_id="v-iso", execution_id="exec-iso", topic="t",
            template="curiosidade", kind="factual", title="T", description="D",
            hashtags=[], script_json={}, total_seconds=10.0,
        )
        # create_video nao retorna nada; verifica leitura.
        got = db.get_video("v-iso")
        assert got is not None
        assert got["video_id"] == "v-iso"
        assert db.db_path == db_path
        assert db_path.exists()
    finally:
        db.close()


def test_database_ambientes_nao_compartilham_dados(tmp_path):
    """Dois ambientes com caminhos distintos nao compartilham vídeos."""
    db_a = Database(tmp_path / "ambiente-a.sqlite3")
    db_b = Database(tmp_path / "ambiente-b.sqlite3")
    try:
        db_a.create_video(
            video_id="v-a", execution_id="exec-a", topic="t",
            template="curiosidade", kind="factual", title="A", description="D",
            hashtags=[], script_json={}, total_seconds=10.0,
        )
        assert db_b.get_video("v-a") is None, (
            "ambiente B nao deve enxergar vídeo do ambiente A"
        )
        assert db_a.get_video("v-a") is not None
    finally:
        db_a.close()
        db_b.close()


def test_graph_orchestrator_repasse_db_path_para_database(tmp_path):
    """GraphOrchestrator repasse db_path para o Database interno."""
    db_path = tmp_path / "graph" / "videos.sqlite3"
    orch = GraphOrchestrator(db_path=db_path)
    try:
        assert orch.db.db_path == db_path
        # Deve ser possível criar e ler vídeo nesse banco isolado.
        orch.db.create_video(
            video_id="v-g", execution_id="exec-g", topic="t",
            template="curiosidade", kind="factual", title="G", description="D",
            hashtags=[], script_json={}, total_seconds=10.0,
        )
        assert orch.db.get_video("v-g") is not None
    finally:
        orch.close()


def test_default_db_path_aponta_para_data_videos_sqlite3():
    """Caminho padrão é data/videos.sqlite3 (sem hardcode de ambiente)."""
    assert DEFAULT_DB_PATH.name == "videos.sqlite3"
    assert DEFAULT_DB_PATH.parent.name == "data"


__all__ = []
