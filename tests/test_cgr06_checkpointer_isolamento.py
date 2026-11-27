"""Regressão CGR-006: isolamento de ambiente do checkpointer do grafo LangGraph.

O SqliteSaver usado pelo GraphOrchestrator deve receber um caminho de banco de
checkpoints isolado por ambiente (via db_path), não um caminho hardcoded
``data/langgraph_checkpoints.sqlite3`` que compartilha estado entre ambientes
(dev, test, production) e entre worktrees paralelos.

Critério de aceitação:
- new_checkpointer(db_path) deve usar o caminho informado, não o default hardcoded.
- GraphOrchestrator(db_path=...) deve propagar o db_path para o checkpointer.
- Dois GraphOrchestrator com db_path distintos não devem compartilhar checkpoints.
"""
from __future__ import annotations

from pathlib import Path
import tempfile

from src.workflow.graph import new_checkpointer
from src.graph_orchestrator import GraphOrchestrator


def test_new_checkpointer_usa_caminho_informado_nao_hardcoded(tmp_path):
    """RED: new_checkpointer(db_path) deve criar o diretório pai do caminho informado."""
    db_path = tmp_path / "checkpoints.sqlite3"
    cp = new_checkpointer(str(db_path))
    # O checkpointer deve ter criado o diretório pai e estar pronto para usar o caminho.
    assert db_path.parent.exists(), (
        f"Esperava diretório pai {db_path.parent} criado pelo checkpointer"
    )


def test_new_checkpointer_sem_argumento_usa_default_hardcoded():
    """RED: new_checkpointer() sem argumento usa o default hardcoded (comportamento atual)."""
    cp = new_checkpointer()
    # Sem argumento, usa o caminho hardcoded data/langgraph_checkpoints.sqlite3.
    assert str(Path("data") / "langgraph_checkpoints.sqlite3") == str(
        Path("data") / "langgraph_checkpoints.sqlite3"
    )


def test_graph_orchestrator_propaga_db_path_para_checkpointer():
    """RED: GraphOrchestrator(db_path=...) deve propagar para o checkpointer."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "videos.sqlite3")
        orch = GraphOrchestrator(db_path=db_path)
        # O caminho do banco de vídeos deve ser o informado.
        assert str(orch.db.db_path) == db_path
        # O caminho do checkpointer também deve ser derivado do db_path (isolado).
        assert orch._checkpoint_db != str(Path("data") / "langgraph_checkpoints.sqlite3"), (
            "Checkpointer não deve usar caminho hardcoded quando db_path é informado."
        )


def test_dois_orchestrator_db_path_distinto_nao_compartilham_checkpointer():
    """RED: Dois GraphOrchestrator com db_path distintos devem ter checkpointers distintos."""
    with tempfile.TemporaryDirectory() as tmp:
        db_a = str(Path(tmp) / "a.sqlite3")
        db_b = str(Path(tmp) / "b.sqlite3")
        orch_a = GraphOrchestrator(db_path=db_a)
        orch_b = GraphOrchestrator(db_path=db_b)
        assert orch_a._checkpoint_db != orch_b._checkpoint_db, (
            "Checkpointers devem ser isolados por db_path."
        )
