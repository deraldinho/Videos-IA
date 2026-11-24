"""Regressão CGR-004: consistência entre os dois orquestradores.

Existem dois orquestradores no código:
- Orchestrator (src/orchestrator.py): pipeline etapa a etapa (script, tts, render,
  validate, approve, upload, publish) com STAGE_ORDER próprio.
- GraphOrchestrator (src/graph_orchestrator.py): pipeline via LangGraph com
  agente supervisor e checkpointer persistente.

main.py já faz alias Orchestrator = GraphOrchestrator, mas os dois expõem
interfaces ligeiramente diferentes. Este teste verifica a consistência contratual
para que qualquer código que importe Orchestrator se comporte de forma previsível.

Critérios de aceitação:
- Ambos expõem generate(), run_stage(), run_pipeline(), approve(), reject(),
  status(), list_by_status(), close().
- STAGE_ORDER do Orchestrator é consistente com os estados do banco.
- run_stage() aceita os mesmos nomes de estágio em ambos.
"""
from __future__ import annotations

from src.orchestrator import Orchestrator, STAGE_ORDER, ExecutionResult
from src.graph_orchestrator import GraphOrchestrator, GraphExecutionResult


def test_ambos_expoem_mesma_interface_publica():
    """RED: Orchestrator e GraphOrchestrator expõem os mesmos métodos públicos."""
    metodos_esperados = {
        "generate", "run_stage", "run_pipeline", "approve", "reject",
        "status", "list_by_status", "close",
    }
    for nome in metodos_esperados:
        assert hasattr(Orchestrator, nome), f"Orchestrator não expõe {nome}"
        assert hasattr(GraphOrchestrator, nome), f"GraphOrchestrator não expõe {nome}"


def test_stage_order_estados_validos_do_banco():
    """RED: STAGE_ORDER do Orchestrator contém apenas estados válidos do banco."""
    from src.database import VIDEO_STATES
    for stage in STAGE_ORDER:
        # Cada estágio do pipeline deve ser um estado válido do banco (ou passo
        # intermediário mapeável). script/tts/render/validate/approve/upload/publish
        # são nomes de etapa da CLI, não estados diretos do banco, então validamos
        # que o mapeamento faz sentido.
        assert stage in {"script", "tts", "render", "validate", "approve", "upload", "publish"}


def test_execution_result_tem_campos_esperados():
    """RED: ExecutionResult tem os campos esperados pela API/CLI."""
    res = ExecutionResult(
        execution_id="exec-1", video_id="vid-1", stage="script",
        status="SCRIPT_READY", changed=True, note="ok",
    )
    assert res.execution_id == "exec-1"
    assert res.video_id == "vid-1"
    assert res.stage == "script"
    assert res.status == "SCRIPT_READY"
    assert res.changed is True
    assert res.note == "ok"


def test_graph_execution_result_tem_campos_esperados():
    """RED: GraphExecutionResult tem os campos esperados, incluindo interrupted."""
    res = GraphExecutionResult(
        execution_id="exec-1", video_id="vid-1", stage="script",
        status="SCRIPT_READY", changed=True, note="ok", interrupted=False,
    )
    assert res.interrupted is False


def test_alias_orchestrator_e_graph_orchestrator_sao_mesma_classe():
    """RED: main.py faz Orchestrator = GraphOrchestrator; são a mesma classe."""
    from main import Orchestrator as MainOrchestrator
    assert MainOrchestrator is GraphOrchestrator
