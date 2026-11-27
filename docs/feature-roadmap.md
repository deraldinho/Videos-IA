# Roadmap de funcionalidades — Videos-IA (CP22–CP28)

> **Estado global:** PLANEJADO. Este documento não atesta código implementado nem testes executados.  
> **Governação:** [AGENTS.md](../AGENTS.md) v3.1. **Arquitetura:** [architecture.md](architecture.md).  
> **Estratégia:** um checkpoint por vez, TDD, testes de regressão e relatório verificável; sem publicação real automática.

## Priorização e dependências

| CP | Prioridade | Entrega | Dependência principal | Estado |
|---|---|---|---|---|
| CP22 | P0 | Pesquisa real + fact-check | Auditoria dos agentes e contratos atuais | PLANEJADO |
| CP23 | P0 | Assets visuais por cena | Mapeamento do pipeline ArtCraft/FilmCraft/HyperFrames | PLANEJADO |
| CP24 | P0 | Publicação multicanal | Estados de publicação e idempotência; CP23 quando necessário | PLANEJADO |
| CP25 | P1 | Analytics real e persistido | Identificadores remotos confiáveis (CP24) | PLANEJADO |
| CP26 | P1 | Experimentos A/B | Métricas com proveniência (CP25) | PLANEJADO |
| CP27 | P1 | Fábrica multicanal + vídeos longos -> Shorts | Assets/filas/adaptadores estabilizados | PLANEJADO |
| CP28 | P2 | Afiliados e receita | Publicação/analytics estáveis e regras comerciais definidas | PLANEJADO |

As dependências podem ser refinadas após auditoria do código. **Não presumir implementação apenas porque existe estrutura nominal**.

## CP22 — Research Intelligence / FactCheck

**Objetivo:** remover a dependência de evidências simuladas para conteúdo factual real, sem eliminar os mocks usados em testes.

**Escopo:** fontes configuráveis/autorizadas, metadados de proveniência, validação de alegações, score de confiabilidade, deduplicação e estados `real`/`não_verificado`/`simulado`.

**Critérios de aceite:**
1. Toda afirmação factual aprovada aponta para evidência verificável e registrada; fontes fictícias não passam no gate.
2. Falha/timeout/ausência de fonte deixa a informação não verificada, sem aprovação automática.
3. Testes cobrem fontes conflitantes, repetidas, indisponíveis, maliciosas e sem data.
4. O fluxo offline anterior permanece testável sem chamadas reais.

**Áreas para inspeção:** `src/workflow/agents/research.py`, `factcheck.py`, `ideas.py`, `scripts.py`.

## CP23 — Scene Asset Intelligence

**Objetivo:** montar recursos visuais originais, consistentes e rastreáveis por cena.

**Escopo:** adaptador visual, prompts por cena, geração opcional por provedor, cache/hash, metadados de licença e custo, fallback seguro.

**Critérios de aceite:**
1. Cada cena possui asset identificado, origem e compatibilidade com o contrato de renderização atual.
2. Reexecuções com os mesmos inputs reaproveitam cache quando seguro, sem perda de rastreabilidade.
3. Falha do provedor não apaga o trabalho anterior nem publica vídeo incompleto.
4. Legendas, duração e sincronização audiovisual passam nos validadores existentes; renderizadores anteriores não sofrem regressão.

**Áreas para inspeção:** `src/workflow/agents/visual.py`, `src/video/`, `templates/`.

## CP24 — Publicação multicanal

**Objetivo:** publicar por destino com recebimento e reconciliação independentes.

**Escopo:** `PublicationAdapter` para TikTok existente, YouTube Shorts prioritário e Instagram Reels condicionado às permissões e APIs vigentes; controle de contas, metadados, autenticação e rate limits.

**Critérios de aceite:**
1. `uploaded`, `queued` e `published` são distinguíveis e comprovados por recibos/estado remoto.
2. Timeout e retry não geram postagem duplicada; publicação ambígua é reconciliada.
3. Falha em um destino não declara os demais publicados ou anula seus recibos.
4. Testes isolados não acessam contas reais; integração remota exige autorização específica.

**Áreas para inspeção:** `src/workflow/agents/publishing.py`, `src/integrations/`, `src/state_machine.py`, `src/database.py`.

## CP25 — Analytics Intelligence

**Objetivo:** medir audiência e custos reais, sem inventar resultados.

**Escopo:** provedores de métricas autorizados, snapshots persistidos, coleta com intervalo configurado, dashboards e observabilidade.

**Critérios de aceite:**
1. Cada métrica tem plataforma, publicação, instante e fonte; ausência de dado não equivale a zero.
2. Dados `real`, `mock` e `estimated` são visivelmente separados.
3. Recoleta é idempotente, preserva histórico e lida com indisponibilidade/rate limit.
4. Dashboards e relatórios reproduzem os números armazenados, com testes de consistência.

**Áreas para inspeção:** `src/workflow/agents/monitoring.py`, `src/metrics.py`, `src/dashboard.py`, `src/database.py`.

## CP26 — Creative Experimentation

**Objetivo:** comparar criativos sem confundir correlação com causalidade.

**Escopo:** hipóteses, variantes de hook/título/CTA/template/duração, grupos/canais comparáveis, janela temporal, análise e decisão editorial.

**Critérios de aceite:**
1. Toda variante referencia o conteúdo base e a alteração experimental.
2. Métricas e janelas comparadas são registradas; fatores de confusão são explicitados.
3. Sem amostra suficiente ou sem dados reais, resultado permanece inconclusivo.
4. Nenhuma variante é publicada automaticamente apenas por participar de um experimento.

## CP27 — Content Factory e cortes longos

**Objetivo:** operar perfis de nicho/canal e produzir Shorts a partir de conteúdo longo autorizado.

**Escopo:** `ChannelProfile`, configurações de voz/visual/orçamento, fila e quotas independentes; transcrição, seleção de cortes, reenquadramento e legendas.

**Critérios de aceite:**
1. Contas e configurações são isoladas; um canal não herda credenciais ou política de outro.
2. Limites, custos e filas são respeitados em execução concorrente e retomada.
3. Cortes preservam contexto e direitos de uso; passam gates de áudio, imagem e legenda.
4. Pipeline existente de vídeos curtos continua funcionando sem novo requisito obrigatório.

## CP28 — Monetization Engine (opcional)

**Objetivo:** permitir conteúdo comercial rastreável e transparente.

**Escopo:** catálogo autorizado, fatos comerciais verificáveis, divulgação de afiliados, links rastreáveis, custos, conversões e margem.

**Critérios de aceite:**
1. Produto, preço e alegações comerciais possuem fonte válida e data de verificação.
2. Conteúdo publicitário e relacionamento de afiliados são identificados apropriadamente.
3. Cliques, conversões e receita só são registrados como reais com fonte confiável.
4. O módulo pode ficar inteiramente desabilitado; nenhum envio comercial não solicitado ou scraping proibido é realizado.

## Quality Gates por checkpoint

- **Pré-edição:** raiz Git, branch, worktree, estado local, diff preexistente e impacto entre consumidores.
- **TDD:** teste de comportamento/contrato antes da implementação, quando aplicável.
- **Integração:** mocks explícitos, falhas remotas, retries, idempotência, persistência e segurança.
- **Regressão:** suíte pertinente, sem desabilitar teste para obter resultado verde.
- **Entrega:** diff revisado, evidências de comandos executados, riscos, arquivos alterados e estado real.
- **GitHub:** commits/push/PR somente sob as permissões do `AGENTS.md`; merge exige autorização específica e todos os gates obrigatórios aprovados.
- **Produção:** ações externas, novas credenciais, chamadas pagas, upload/deploy/publicação real exigem escopo autorizado, nunca decorrem apenas deste roadmap.

## Regra de execução

Executar CP22 primeiro após auditoria, depois CP23 e CP24; não implementar todos os checkpoints em paralelo sobre os mesmos módulos. Encerrar cada entrega com relatório: checkpoint, escopo, arquivos, testes, resultados, riscos, branch/commit/PR (quando existirem) e próximo passo.
