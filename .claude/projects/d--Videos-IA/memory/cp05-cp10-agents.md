---
name: cp05-cp10-agents
description: CP05-CP10 completo: 12 agentes especializados no LangGraph (research, idea, factcheck, script, voice, visual, render, transcription, quality, compliance, publishing, monitoring, audit), grafo expandido de 5 para 15 nós, todos os testes passando
metadata:
  node_type: memory
  type: project
  originSessionId: 906dbc65-0e55-4c92-8d4e-9a9d2cfcaeaa
  modified: 2026-11-20T23:50:00.000Z
---

CP05-CP10: 12 agentes especializados implementados no `src/workflow/agents/`.

**Agentes (10 novos + 2 existentes):**
- `research_agent` — coleta fontes/dados sobre o tópico (determinístico, mock)
- `idea_agent` — gera ideia original de roteiro a partir da pesquisa
- `factcheck_agent` — verifica fatos e fontes (mock)
- `script_agent` — confirma/gera roteiro (existente, CP04)
- `voice_agent` — narração TTS Piper pt-BR com fallback de silêncio
- `visual_agent` — storyboard/layout/elementos visuais
- `render_agent` — renderização MP4 1080×1920 30fps H.264/AAC via HTMLRenderer+FFmpeg
- `transcription_agent` — transcrição/ASR do áudio gerado (mock)
- `quality_agent` — validação FFprobe (existente, CP04)
- `compliance_agent` — regras DRY_RUN/REVIEW/AUTO, aprovação, bloqueio de publicação
- `publishing_agent` — upload Cloudinary + agendamento Buffer (mock sem live)
- `monitoring_agent` — métricas, custos, erros, auditoria
- `audit_agent` — entrada final de auditoria (existente, CP04)

**Grafo:** `src/workflow/graph.py` expandido de 5 para 15 nós (supervisor + 13 agentes + human_approval). Supervisor roteia determinísticamente por estágio. Checkpoints SqliteSaver. Interrupção humana via `interrupt()`/`Command(resume=...)`.

**Estado:** `src/workflow/state.py` expandido com campos `kind`, `mode`, `live_enabled`, `auto_enabled`, `idea`, `factcheck`, `compliance`, `monitoring`, `cloudinary_url`, `cloudinary_id`, `buffer_post_id`, `buffer_post_state`.

**Testes:** `tests/test_workflow_graph.py` — 36 testes (supervisor roteamento, agentes unitários, grafo com interrupt/resume, round-trip estado). Todos passando.

**Related:** [[cp01-baseline]], [[cp04-graph]], [[cp11-resilience]], [[cp12-observability]]
