"""Agente de pesquisa: coleta fontes e dados para o tema do vídeo."""

from __future__ import annotations

from typing import Any

from src.workflow.agents.base import BaseAgent
from src.workflow.state import Stage


class ResearchAgent(BaseAgent):
    """Pesquisa fontes e dados sobre o tópico antes da geração do roteiro.

    Responsabilidades (CP05):
    - Recebe o ``topic`` do estado.
    - Gera ``research_sources`` (lista de dicts com fonte, url, resumo).
    - **Modo atual (offline/mock):** produz fontes simuladas/determinísticas
      para que o pipeline rode offline sem chamadas de rede.
    - **Para produção:** substituir a seção de fontes simuladas por chamadas a
      um motor de busca real (ex.: Google Custom Search, Bing, Brave Search) e
      preencher ``url``, ``title`` e ``summary`` com dados reais. Cada fonte
      deve conter uma URL verificável e um resumo extraído do conteúdo real.
    - Avança o status para IDEA_RESEARCHED (mapeado internamente via
      current_stage) e devolve o estado completo.

    ⚠️ Limitação de segurança: nenhuma chamada de rede externa é feita por
    padrão. Ative a verificação real apenas com credenciais e autorização
    explícita do operador.
    """

    def __init__(self) -> None:
        super().__init__("ResearchAgent")

    def run(self, state: dict[str, Any]) -> dict[str, Any]:
        state = self._ensure_state(state)
        topic = state.get("topic") or "a definir"

        # Fontes simuladas (determinísticas por topic) — substituir por busca real.
        sources = [
            {
                "source": "pesquisa_web",
                "title": f"Contexto sobre {topic}",
                "url": f"https://exemplo.com/pesquisa?q={topic.replace(' ', '+')}",
                "summary": f"Resumo pesquisado sobre {topic} (mock).",
                "relevance": 0.9,
            },
            {
                "source": "dados_publicos",
                "title": f"Dados de referência: {topic}",
                "url": "https://exemplo.com/dados",
                "summary": "Dados públicos de suporte (mock).",
                "relevance": 0.7,
            },
        ]

        state["research_sources"] = sources
        state["current_stage"] = "IDEA_RESEARCHED"
        state["status"] = "IDEA_RESEARCHED"
        state["_next"] = "supervisor"
        state["_step"] = "research_agent"
        self._add_cost(state, self.name, "pesquisa", tokens=len(topic) * 2, cost_usd=0.0)
        return self._mark(state, self.name, "research",
                          f"{len(sources)} fontes coletadas para '{topic}'",
                          topic=topic, source_count=len(sources))
