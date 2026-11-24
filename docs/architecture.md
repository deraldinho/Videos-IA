# Arquitetura evolutiva — Videos-IA

> **Status:** proposta técnica para CP22–CP28; não representa funcionalidades já implementadas.  
> **Autoridade operacional:** [AGENTS.md](../AGENTS.md) v3.1.  
> **Plano de execução:** [feature-roadmap.md](feature-roadmap.md).  
> **Fontes locais consultadas:** `README.md`, `src/workflow/`, `src/video/`, `src/integrations/`, `src/database.py`, `src/scheduler.py`.

## 1. Estado atual e limites

A base atual utiliza Python, Pydantic, LangGraph, SQLite, FFmpeg/FFprobe, renderização HTML/Puppeteer, Piper TTS e adaptadores Cloudinary/Buffer. Há recursos locais envolvendo ArtCraft/FilmCraft (`src/video/producao_local.py`) e HyperFrames (`src/video/hyperframes_adapter.py`).

O pipeline documentado usa pesquisa, ideia, fact-check, roteiro, voz, visual, renderização, transcrição, qualidade, conformidade, aprovação humana, publicação, monitoramento e auditoria. Os módulos e contratos reais prevalecem sobre esta descrição; confirmar consumidores antes de implementar.

**Lacunas observadas:** `ResearchAgent` e `FactCheckAgent` usam dados determinísticos simulados; `MonitoringAgent` acompanha execução/custos, não métricas completas de audiência. O `PublishingAgent` distingue agendamento de publicação, e o modo offline não deve registrar `PUBLISHED` como confirmação real. A implementação de conectores e provedores propostos abaixo permanece **pendente**.

## 2. Princípios de evolução

1. **Uma única orquestração:** preservar o LangGraph e estender agentes existentes antes de considerar novos nós; não criar um segundo fluxo de produção.
2. **Contratos primeiro:** verificar Pydantic/SQLite/consumidores atuais; introduzir versões de esquema aditivas e migrações reversíveis.
3. **Ports/adapters:** fontes de pesquisa, recursos visuais, plataformas e métricas são implementações intercambiáveis, acionadas somente por configuração autorizada.
4. **Side effects controlados:** processamento local, upload, agendamento e publicação são fases distintas. Nenhum efeito externo real em testes padrão.
5. **Evidência auditável:** todos os estados críticos incluem origem, carimbo temporal, identidade de execução e evidência de confirmação.
6. **Compatibilidade:** ArtCraft, FilmCraft, HyperFrames, Piper, FFmpeg, Buffer e Cloudinary permanecem operacionais nos caminhos existentes.

## 3. Fluxo lógico de destino (proposta)

```text
Fontes autorizadas -> ResearchAgent -> FactCheckAgent -> Idea/Script
    -> SceneAssetProvider -> Voice/Visual -> Render/Transcription/Quality
    -> Compliance -> Aprovação humana -> PublicationAdapter por canal
    -> Verificação remota -> MetricSnapshot -> Experimentação/insights
                                               \-> novas hipóteses editoriais
```

Nenhuma seta autoriza uma ação externa. O supervisor e a máquina de estados atuais controlam a transição efetiva.

## 4. Contratos conceituais

Os contratos são **propostos**, não classes existentes. Validar nomenclatura, schemas, serialização e compatibilidade antes de implementá-los.

| Contrato | Campos conceituais mínimos | Invariantes |
|---|---|---|
| `ResearchEvidence` | `source_id`, `url`, `retrieved_at`, `claim`, `evidence_type`, `verification_status` | URLs fictícias/mocks nunca são evidências reais; ausência de fonte não vira aprovação |
| `SceneAsset` | `asset_id`, `scene_id`, `uri`, `sha256`, `media_type`, `origin`, `provider`, `license`, `cost` | Asset rastreável, uso permitido, idempotência por conteúdo e configuração |
| `PublicationTarget` | `target_id`, `video_id`, `platform`, `channel_id`, `idempotency_key`, `local_state`, `remote_id`, `remote_status`, `confirmed_at` | Confirmação inequívoca de plataforma antes de `PUBLISHED` |
| `MetricSnapshot` | `target_id`, `observed_at`, `source`, `provenance`, `metrics` | `real`, `estimated`, `mock` e `unavailable` não se misturam; instantâneos imutáveis |
| `Experiment` | `experiment_id`, `hypothesis`, `variants`, `measurement_window`, `metric_definition`, `decision_status` | Sem conclusão estatística sem amostra e comparação adequadas |
| `ChannelProfile` | `channel_id`, `platform`, `niche`, `language`, `visual_preset`, `budget`, `publication_policy` | Isolamento de permissões, cotas e configuração |

Não salvar senhas, tokens ou cookies em tabelas de domínio; utilizar mecanismo seguro de segredos já autorizado.

## 5. Interfaces propostas e reutilização

- **`ResearchSource`**: consulta fontes permitidas com resultado tipado e proveniência; `ResearchAgent` permanece responsável pela consolidação.
- **`SceneAssetProvider`**: gera/seleciona imagens e vídeos por cena; `VisualAgent` orquestra, renderizadores existentes continuam compondo o resultado.
- **`PublicationAdapter`**: `prepare`, `upload`, `schedule/publish`, `get_remote_status`; `PublishingAgent` decide transições e persiste recibos.
- **`AnalyticsProvider`**: consulta métricas disponíveis, com fonte, instante e permissões; `MonitoringAgent` consolida snapshots.
- **`ExperimentEvaluator`**: compara variantes com limitações explícitas, sem gerar métricas fictícias.

Antes de criar cada interface, buscar extensão adequada nos módulos `src/integrations/`, `src/video/` e `src/workflow/agents/`.

## 6. Máquina de estados e publicação remota

- Preservar os estados globais atuais e acrescentar estado por destino quando necessário, sem quebrar consumidores.
- `UPLOADED` comprova somente transferência; `QUEUED` comprova somente agendamento; `PUBLISHED` exige evidência remota real.
- Confirmar identificador da publicação **da operação específica** (não assumir que o último vídeo listado corresponde ao upload).
- Registrar tentativas, resultado, erro recuperável, recibo e chave de idempotência.
- Timeouts e falhas ambíguas levam a reconciliação, nunca a reenvio cego ou falso sucesso.
- Cada canal tem permissões OAuth/API, rate limiting, orçamento e política editorial próprios.
- Manter controles `DRY_RUN`/`REVIEW`/`AUTO`, `live_enabled`, `auto_enabled` e aprovação humana.

## 7. Persistência, segurança e implantação

- Adotar migrações aditivas testadas com cópia sintética do SQLite; preservar registros históricos.
- Separar `real`, `mock`, `estimated` e `unavailable` em dados, APIs, dashboards e relatórios.
- Segredos nunca entram em logs, fixtures, GitHub ou artefatos. Não ler nem divulgar `.env` na auditoria.
- Respeitar licenças e direitos de uso de modelos, mídia e código; não copiar componentes AGPL-3.0 sem autorização.
- Não habilitar chamadas pagas, scraping vedado, publicação ou distribuição externa por padrão.
- Para provedores externos, validar as condições de serviço e capacidades na data de implementação; capacidades podem variar por conta e região.

## 8. Estratégia de testes e observabilidade

- Testes unitários de interfaces e estados com mocks determinísticos.
- Testes de contrato para validadores, provedores, reconciliação e serialização.
- Testes de migração, concorrência/idempotência, retries, ausência de áudio e qualidade audiovisual.
- Integração em sandbox somente com autorização explícita; produção não é ambiente de teste.
- Logs estruturados com `execution_id`, `video_id`, `channel_id` (não sensível), `target_id`, fase, latência e resultado.
- Gate por checkpoint: diff revisado, testes reais executados quando pertinentes, regressão, segurança, evidências e relatório conforme `AGENTS.md`.

## 9. Fora do escopo desta documentação

Nenhuma implementação, migração, conexão externa, alteração de credenciais, deploy, publicação de vídeo, commit, push ou merge está autorizada por este documento. A execução efetiva exige missão com escopo e permissões correspondentes.
