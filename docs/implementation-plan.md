# Plano de Implementação — TikTok AI Autopilot

**Metodologia:** Superpowers (Fases A–E)  
**Status:** Pendente aprovação do desenho inicial  
**Local:** `tiktok-automation/docs/implementation-plan.md`

---

## Fase A — Brainstorming (concluída no diagnóstico)

### Requisitos examinados
Pipeline completo: ideias → roteiro → narração → HTML/CSS → render MP4 → validação → aprovação → Cloudinary → Buffer → TikTok → monitoramento. Três modos: DRY_RUN, REVIEW (padrão), AUTO (exige ativação explícita).

### Riscos e dependências identificados
| Risco | Impacto | Mitigação |
|-------|---------|-----------|
| FCC-Claude não acessível de forma não interativa por script Python agendado | Bloqueia geração autônoma de roteiros | Interface `LLMProvider` com modo `assisted` (salva JSON para revisão humana) + opcional NVIDIA NIM apenas com chave autorizada pelo usuário |
| Nenhuma skill HyperFrames instalada localmente | Não há contrato oficial verificado para renderização | Implementar renderer próprio com Node + headless Chrome/Playwright + FFmpeg como fallback, documentando a lacuna |
| code-review-graph / Graphify / Superpowers podem não ser compatíveis com FCC-Claude | Falha na revisão estrutural | Usar CLI das ferramentas quando disponível; documentar incompatibilidade e alternativa local |
| Publicação duplicada por timeout no Buffer | Viola política da plataforma | Verificar estado remoto antes de retry; idempotência por ID de vídeo + public_id do Cloudinary |
| Credenciais expostas | Segurança | `.env` no `.gitignore`, `.env.example` sem credenciais, variáveis de ambiente apenas |
| MP4 corrompido ou fora do padrão TikTok | Rejeição pela plataforma | Validação com FFprobe antes de qualquer upload (resolução, duração, codec, áudio, legendas) |
| Fuso horário America/Sao_Paulo / DST | Agendamento errado | `zoneinfo` com conversão explícita e testes de fuso |

### Limites dos serviços gratuitos
- **Cloudinary (plano gratuito):** cota de armazenamento e largura de banda; uploads duplicados devem ser evitados. O plano gratuito tem limites específicos de vídeo que precisam ser monitorados.
- **Buffer (plano gratuito):** máximo de 10 agendamentos pendentes por canal; rate limits da API GraphQL; aceitação de agendamento ≠ publicação confirmada no TikTok.
- **TikTok:** alguns tipos de post exigem confirmação no app (`ACTION_REQUIRED`).
- **Piper TTS:** local, gratuito, offline (quando voz pt-BR disponível).

### Critérios mensuráveis de sucesso (aceitação da v1)
1. Projeto instalável pelo README.
2. `python main.py generate` produz roteiro + vídeo em modo local/assistido.
3. Ao menos um MP4 de teste produzido e validado (FFprobe: 1080×1920, 30 fps, áudio presente, duração esperada).
4. Fila persistente em SQLite funcionando (estados validados).
5. Modos DRY_RUN e REVIEW implementados.
6. Clientes Cloudinary e Buffer com testes mockados.
7. Scheduler configurável.
8. Proteção contra duplicidade testada.
9. Code Review Graph analisou o código.
10. Graphify produziu o mapa de arquitetura.
11. Nenhuma publicação real sem autorização explícita.

---

## Fase B — Planejamento de implementação

### Estrutura adotada (adaptação da solicitada)
```
tiktok-automation/
├── .claude/CLAUDE.md              # convenções do projeto (não sobrescreve regras globais)
├── config/
│   ├── config.yaml                # modos, tempos, fuso, filas, limites
│   └── templates.yaml             # metadados dos templates de vídeo
├── src/
│   ├── __init__.py
│   ├── errors.py                  # PipelineError, DuplicateError
│   ├── settings.py                # carregamento config.yaml + .env (tipagem)
│   ├── database.py                # SQLite: vídeos, estados, histórico, agendamentos
│   ├── state_machine.py           # transições de estado validadas + idempotência
│   ├── orchestrator.py            # execução por etapa com ID de execução único
│   ├── scheduler.py               # slots de produção/publicação, fuso SP, tarefa do SO
│   ├── ai/
│   │   ├── provider.py            # LLMProvider (assisted / nvidia_nim)
│   │   ├── ideas.py               # geração de ideias, evita repetidos
│   │   └── scripts.py             # roteiros PT-BR, 30-60s, gancho, CTA, hashtags
│   ├── video/
│   │   ├── tts.py                 # Piper TTS local (pt-BR) ou fallback silencioso
│   │   ├── renderer.py            # HyperFrames/HTML→MP4 (Node + FFmpeg)
│   │   └── validator.py           # FFprobe: resolução, duração, codec, áudio, integridade
│   ├── integrations/
│   │   ├── cloudinary_client.py   # upload, URL pública, dedup, limite gratuito
│   │   └── buffer_client.py       # GraphQL Buffer, canal TikTok, fila, estados
│   └── cli.py                     # argumentos: generate, review, approve, schedule, status, run-daily
├── templates/
│   ├── curiosity.html             # template curiosidades
│   ├── list.html                  # template listas/rankings
│   ├── story.html                 # template histórias curtas
│   ├── explainer.html             # template explicações tecnológicas
│   └── qa.html                    # template perguntas e respostas
├── tests/
│   ├── test_settings.py
│   ├── test_database.py
│   ├── test_state_machine.py
│   ├── test_scheduler.py
│   ├── test_scripts.py
│   ├── test_validator.py
│   └── test_integrations_mock.py
├── data/
│   └── inbox/.gitkeep             # roteiros entregues pelo FCC-Claude (modo assisted)
├── output/
│   ├── drafts/
│   ├── approved/
│   ├── published/
│   └── failed/
├── logs/
├── graphify-out/                  # artefatos Graphify (gitignorado)
├── .env.example                   # sem credenciais reais
├── .gitignore                     # protege segredos, mídias, dependências
├── .graphifyignore                # exclui .env, mídias, output, node_modules
├── .code-review-graphignore       # exclui dados sensíveis e saídas
├── config.yaml
├── requirements.txt
├── requirements-dev.txt
├── main.py                        # entrypoint CLI
└── README.md
```

### Tarefas e dependências (sequência para economizar tokens)

| ID | Tarefa | Depende | Critério de aceitação |
|----|--------|---------|----------------------|
| T1 | Configurar settings.py, database.py, state_machine.py | — | SQLite cria tabelas; transições válidas; idempotência por ID |
| T2 | Implementar provider.py, ideas.py, scripts.py (modo assisted) | T1 | `LLMProvider.complete()` retorna JSON; roteiro tem gancho, cenas, duração, CTA, hashtags |
| T3 | Implementar tts.py (Piper local) + renderer.py (HTML→MP4 via Node/FFmpeg) | T1 | TTS gera áudio pt-BR; renderer produz MP4 1080×1920 30fps |
| T4 | Implementar validator.py (FFprobe) | T3 | Valida resolução, duração, codec, áudio, integridade; rejeita corrompido |
| T5 | Implementar cloudinary_client.py + buffer_client.py com mocks | T1 | Upload simulado, URL pública, dedup, fila, estados; sem chamadas reais |
| T6 | Implementar orchestrator.py (fluxo por etapa, ID de execução único) | T1-T5 | Pipeline executa etapa a etapa; não avança em erro; retoma após falha |
| T7 | Implementar scheduler.py + tarefa do SO (Windows Task Scheduler / cron) | T1, T6 | Slots 12:00 e 19:00, fuso SP, 2/dia, configurável, não depende do VS Code |
| T8 | Implementar main.py CLI (generate, review, approve, schedule, status, run-daily) | T1-T7 | Comandos funcionam; erros compreensíveis; não publica sem aprovação em REVIEW |
| T9 | Criar templates HTML/CSS/JS (5 tipos) | T3 | Templates reutilizáveis com variáveis de texto/cor/animação |
| T10 | Escrever testes unitários + integração (mocks para APIs externas) | T1-T8 | `pytest` passa; mocks simulam Cloudinary/Buffer timeout e estados |
| T11 | Gerar vídeo demonstrativo de 15s (curiosidade tecnológica) e validar com FFprobe | T3, T4, T9 | MP4 válido, texto animado, transições, narração pt-BR quando TTS operacional |
| T12 | Rodar Code Review Graph sobre o código | T1-T11 | Grafo atualizado; mudanças analisadas; regressões corrigidas |
| T13 | Rodar Graphify para mapa de arquitetura | T1-T11 | `graphify-out/graph.html`, `graph.json`, `GRAPH_REPORT.md` gerados localmente |
| T14 | Escrever README.md em português | T1-T13 | Instalação, comandos, configuração NVIDIA, Cloudinary, Buffer, TikTok, agendador, aprovação, logs, limites gratuitos, desativação |
| T15 | Revisão final Superpowers (verificação antes de conclusão) | T1-T14 | Evidências de testes; resumo de arquivos; limitações reportadas |

---

## Fase C — Desenvolvimento (TDD, quando aplicável)

Para cada módulo:
1. Definir comportamento esperado.
2. Escrever teste (falha inicial esperada).
3. Implementar solução mínima.
4. Executar teste novamente.
5. Refatorar sem quebrar comportamento.
6. Revisar.

Subagentes só quando houver benefício mensurável e sem custos inesperados. Execução sequencial preferida.

---

## Fase D — Revisão (não avançar com defeitos críticos conhecidos)

- `code-review-graph build` / `update --brief` / `detect-changes --brief` / `status`
- Análise de impacto antes de modificar módulos existentes.
- Regressões corrigidas antes de avançar.

---

## Fase E — Entrega

Cada etapa concluída com:
- Evidências concretas de testes.
- Resumo de arquivos modificados.
- Limitações reportadas explicitamente.

---

## Próximo passo após aprovação

Iniciar **T1** (config, database, state_machine) em seguida, sem interrupções para decisões técnicas triviais. Decisões não triviais serão trazidas a você antes de executar.

---

## Declaração de não-publicação real

Nenhuma operação real contra Cloudinary, Buffer ou TikTok será executada durante esta implementação. Todas as integrações externas usarão mocks em testes até que você forneça credenciais e autorize explicitamente.