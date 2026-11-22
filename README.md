# TikTok AI Autopilot

Pipeline completo, modular e gratuito para automação de vídeos curtos do TikTok, rodando localmente no Windows 11.

```
IDEIAS → ROTEIRO → NARRAÇÃO → HTML/CSS → RENDERIZAÇÃO MP4 → VALIDAÇÃO → APROVAÇÃO → CLOUDINARY → BUFFER → TIKTOK → MONITORAMENTO
```

## Modos de operação

| Modo | Descrição | Publicação real |
|------|-----------|-----------------|
| `DRY_RUN` | Tudo simulado, nada sai do disco | Não |
| `REVIEW` (padrão) | Gera e valida; aguarda aprovação humana antes de publicar | Não (salvo se `live_enabled: true`) |
| `AUTO` | Pipeline autônomo end-to-end | Só se `auto_enabled: true` + `live_enabled: true` |

**Nunca publique sem autorização explícita.** O modo `AUTO` exige `auto_enabled: true` no `config.yaml` e só envia para Cloudinary/Buffer quando `live_enabled: true`.

## Requisitos

- Windows 11
- Python 3.10+ (recomendado 3.14)
- [FFmpeg](https://ffmpeg.org/download.html) (ffmpeg + ffprobe no PATH)
- [Node.js](https://nodejs.org/) (para renderização HTML→MP4 via headless Chromium)
- [Piper TTS](https://github.com/rhasspy/piper) com modelo de voz pt-BR (opcional; fallback de silêncio se ausente)
- Conta gratuita [Cloudinary](https://cloudinary.com/) (para upload de MP4)
- Conta [Buffer](https://buffer.com/) com API key (para agendamento no TikTok)
- Acesso ao [FCC-Claude](https://claude.ai) (modo `assisted`) ou cota NVIDIA NIM (modo `nvidia_nim`)

## Instalação

```powershell
# Clone ou baixe o projeto
cd D:\Videos-IA\tiktok-automation

# Crie o ambiente virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Instale dependências
pip install -r requirements.txt

# Copie o template de variáveis de ambiente e preencha com suas credenciais
Copy-Item .env.example .env
# Edite .env com CLOUDINARY_*, BUFFER_API_KEY, NVIDIA_API_KEY (opcional)
```

> ⚠️ Nunca coloque credenciais reais no `config.yaml` nem no código. Use apenas o `.env` (já no `.gitignore`).

## Estrutura do projeto

```
tiktok-automation/
├── main.py                     # CLI: generate, review, approve, schedule, status, run-daily,
│                               #         tick, health, dashboard, metrics
├── config.yaml                 # Modos, tempos, fuso, filas, limites
├── .env.example                # Template de variáveis de ambiente (sem segredos)
├── requirements.txt            # Dependências Python
├── src/
│   ├── settings.py             # Carrega config.yaml + .env (Settings imutável)
│   ├── database.py             # SQLite: vídeos, estados, histórico, agendamentos
│   ├── state_machine.py        # Transições de estado validadas + idempotência
│   ├── orchestrator.py         # Execução etapa a etapa com ID de execução único
│   ├── scheduler.py            # Slots de produção/publicação (fuso SP, DST)
│   ├── errors.py               # PipelineError, DuplicateError
│   ├── ideas.py                # LLMProvider Protocol + build_prompt (PT-BR)
│   ├── scripts.py              # Roteiros PT-BR: validação, fontes, fingerprint
│   ├── ai/provider.py          # Provedores: assisted (FCC-Claude) / nvidia_nim
│   ├── logging.py              # Logging JSON estruturado com filtro de secrets
│   ├── metrics.py              # Métricas operacionais em memória (thread-safe)
│   ├── circuit_breaker.py      # Circuit breaker CLOSED/OPEN/HALF_OPEN
│   ├── retry.py                # retry_call com exponential backoff
│   ├── health.py               # Health checks (FFmpeg, banco, credenciais)
│   ├── dashboard.py            # Dashboard HTML estático com fila, métricas, erros
│   ├── video/tts.py            # Piper TTS local (pt-BR) com fallback de silêncio
│   ├── video/renderer.py       # HTML→MP4 via Node + Chromium headless + FFmpeg
│   ├── video/validator.py      # FFprobe: 1080×1920, 30 fps, áudio, duração
│   ├── workflow/
│   │   ├── __init__.py         # Exporta agentes e grafo
│   │   ├── state.py            # WorkflowState (Pydantic) + empty_state com novos campos
│   │   ├── graph.py            # LangGraph StateGraph: supervisor + 12 agentes +
│   │   │                       human_approval + audit (15 nós), SqliteSaver
│   │   └── agents/             # 12 agentes especializados:
│   │       ├── base.py         # BaseAgent ABC (_ensure_state, _mark, _add_error, _add_cost)
│   │       ├── research.py     # Coleta fontes/dados sobre o tópico
│   │       ├── idea.py         # Gera ideia original de roteiro
│   │       ├── factcheck.py    # Verificação de fatos e fontes
│   │       ├── script.py       # Confirma/gera roteiro (CP04)
│   │       ├── voice.py        # TTS Piper pt-BR + fallback silêncio
│   │       ├── visual.py       # Storyboard/layout/template map
│   │       ├── render.py       # MP4 1080×1920 30fps H.264/AAC via FFmpeg
│   │       ├── transcription.py# ASR/transcrição do áudio
│   │       ├── quality.py      # Validação FFprobe (CP04)
│   │       ├── compliance.py   # Regras DRY_RUN/REVIEW/AUTO, aprovação, bloqueio
│   │       ├── publishing.py   # Cloudinary upload + Buffer agendamento (mock)
│   │       └── monitoring.py   # Métricas, custos, erros, observação
│   └── integrations/
│       ├── cloudinary_client.py # Upload, URL pública, dedup, cota gratuita
│       └── buffer_client.py     # GraphQL Buffer: canal TikTok, fila, estados
├── templates/                  # Templates HTML/CSS (curiosidade, lista, historia, explicacao, qa)
├── tests/                      # 142 testes unitários (pytest): CP01-CP12
├── data/inbox/                 # Roteiros entregues pelo FCC-Claude (modo assisted)
├── output/                     # drafts/, approved/, published/, failed/
├── ops/
│   ├── install-windows.ps1     # Tarefa TikTokAutomation no Agendador do Windows
│   └── install-cron.sh         # Linha de cron (não modifica crontab automaticamente)
├── docs/implementation-plan.md # Plano Superpowers (Fases A–E, T1–T15)
└── graphify-out/               # Mapa de arquitetura (Graphify, gitignorado)
```

## Comandos CLI

```powershell
# Gera um novo vídeo e roteiro (modo assisted: aguarda JSON do operador via FCC-Claude)
python main.py generate --topic "curiosidade tecnológica" --execution-id exec-001

# Lista vídeos aguardando aprovação humana
python main.py review

# Aprova um vídeo para publicação (exige estado AWAITING_APPROVAL)
python main.py approve exec-001

# Mostra próximos slots de publicação disponíveis
python main.py schedule

# Status de um vídeo ou da fila completa
python main.py status exec-001
python main.py status

# Executa o pipeline diário (produção + publicação; usado pelo agendador)
python main.py run-daily

# Health check do sistema (FFmpeg, banco, credenciais)
python main.py health

# Gera o dashboard administrativo HTML estático
python main.py dashboard --output output/dashboard.html

# Mostra métricas operacionais coletadas em memória
python main.py metrics

# Tick do agendador (alias de run-daily)
python main.py tick

# Ajuda
python main.py --help
```

## Máquina de estados

14 estados válidos com transições restritas:

```
IDEA_CREATED → SCRIPT_READY → RENDERING → RENDERED → VALIDATED →
AWAITING_APPROVAL → APPROVED → UPLOADING → UPLOADED → QUEUED →
PUBLISHING → PUBLISHED
                              ↑_________________________│
                              │                          ↓
                              └─────────── FAILED ◄──────┘  (retomável)
```

Estados terminais: `PUBLISHED`, `FAILED`. Apenas `APPROVED` pode avançar para upload.

## Configuração (`config.yaml`)

```yaml
mode: REVIEW               # DRY_RUN | REVIEW | AUTO
live_enabled: false        # Ativa operações reais (Cloudinary/Buffer)
auto_enabled: false        # Obrigatório se mode: AUTO
niche: curiosidades e explicações sobre tecnologia do cotidiano
script_kind: factual       # factual | ficcao (fatos exigem aprovação humana)

llm:
  provider: assisted        # assisted (FCC-Claude) | nvidia_nim
  allow_nvidia_requests: false
  nvidia_model: nvidia/nemotron-3.5-super-vl-120b-a12b

video:
  min_seconds: 30
  max_seconds: 60
  target_seconds: 45
  width: 1080
  height: 1920
  fps: 30
  piper_model: models/pt_BR-faber-medium.onnx

publication:
  timezone: America/Sao_Paulo
  publish_times: ['12:00', '19:00']
  videos_per_day: 2
  queue_limit: 10          # Teto do plano gratuito do Buffer por canal
  horizon_days: 7

cloudinary:
  max_video_mb: 25
  stop_usage_percent: 85
```

## Variáveis de ambiente (`.env`)

```ini
CLOUDINARY_CLOUD_NAME=
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=
BUFFER_API_KEY=
BUFFER_ORGANIZATION_ID=
BUFFER_CHANNEL_ID=
NVIDIA_API_KEY=
NVIDIA_MODEL=
```

## Agendamento (Windows Task Scheduler)

Instale a tarefa `TikTokAutomation` (desabilitada por padrão):

```powershell
.\ops\install-windows.ps1 -Enable   # registra e habilita
.\ops\install-windows.ps1           # registra desabilitado (padrão)
```

A tarefa roda `main.py tick` a cada 15 minutos. O próprio `main.py run-daily` decide se há trabalho (dia de produção, horário de publicação, limite diário).

Em Linux/macOS, use `ops/install-cron.sh` (imprime a linha de cron; você adiciona manualmente via `crontab -e`).

## Testes

```powershell
pytest tests/ -v
```

Suíte: **142 testes** cobrindo todos os CPs (CP01-CP12):
- CP01 baseline: settings, database, state_machine, scheduler, scripts, ai provider, integrações (mock), vídeo (TTS/validator), orchestrator (73 testes)
- CP02: P0/P1 defeitos corrigidos + testes de regressão (10 testes)
- CP03: contratos Pydantic tipados (WorkflowState, Script, AudioAsset, VideoAsset, etc.)
- CP04: LangGraph supervisor + 5 agentes, checkpoints SqliteSaver, interrupt humano (11 testes)
- CP05-CP10: 12 agentes especializados, grafo de 15 nós, roteamento por estágio (36 testes)
- CP11: logging JSON, métricas, circuit breaker, retry, health checks, dashboard HTML (9 testes)
- CP12: observabilidade, dashboard administrativo, auditoria final (13 testes)

Zero regressões entre CPs.

## Arquitetura LangGraph (Supervisor + 12 Agentes)

O pipeline roda sobre um `StateGraph` do LangGraph com checkpoints `SqliteSaver` (SQLite) e interrupção humana via `interrupt()` / `Command(resume=...)`. O supervisor roteia determinísticamente por estágio:

```
IDEA_CREATED → research → idea → factcheck → script → voice → visual → render →
transcription → quality → compliance → human_approval → publishing → monitoring → audit
```

Estados e transições são válidos pela máquina de estados (`state_machine.py`) com `ALLOWED_TRANSITIONS`. Apenas `APPROVED` pode avançar para upload. `FAILED` é retomável.

### Agentes especializados

| Agente | Responsabilidade |
|--------|------------------|
| `research` | Coleta fontes/dados sobre o tópico (mock determinístico) |
| `idea` | Gera ideia original de roteiro a partir da pesquisa |
| `factcheck` | Verificação de fatos e fontes |
| `script` | Confirma/gera roteiro (contrato Pydantic `Script`) |
| `voice` | TTS Piper pt-BR com fallback de silêncio |
| `visual` | Storyboard/layout, template map (curiosidade→curiosity) |
| `render` | MP4 1080×1920 30fps H.264/AAC via HTMLRenderer + FFmpeg |
| `transcription` | ASR/transcrição do áudio gerado (mock) |
| `quality` | Validação FFprobe (duração, áudio, resolução) |
| `compliance` | Regras DRY_RUN/REVIEW/AUTO, aprovação, bloqueio de publicação |
| `publishing` | Cloudinary upload + Buffer agendamento (mock sem live) |
| `monitoring` | Métricas, custos, erros, observação |

## Observabilidade

- **Logging estruturado JSON** (`src/logging.py`) com `_SecretFilter` que nunca grava credenciais nos logs.
- **Métricas em memória** (`src/metrics.py`): counters, gauges, histograms — vídeos por status, execuções por agente, erros por estágio, custos de tokens, durações.
- **Circuit breaker** (`src/circuit_breaker.py`): estados CLOSED/OPEN/HALF_OPEN com fail_threshold e recovery_timeout.
- **Retry** (`src/retry.py`): exponential backoff com max_attempts e callback on_retry.
- **Health checks** (`src/health.py`): FFmpeg, banco SQLite, credenciais (modo live/AUTO).
- **Dashboard HTML** (`src/dashboard.py`): fila de vídeos por status, vídeos recentes, erros, health check, métricas — acessível via `python main.py dashboard`.

## Não-publicação real

Nenhuma operação real contra Cloudinary, Buffer ou TikTok é executada durante o desenvolvimento. Todas as integrações externas usam mocks até que você forneça credenciais e autorize explicitamente via `live_enabled: true`. O modo `AUTO` exige `auto_enabled: true` + `live_enabled: true`.

## Limites dos serviços gratuitos

- **Cloudinary (gratuito):** cota de armazenamento e largura de banda. O cliente para de uploads ao atingir `stop_usage_percent` (default 85%).
- **Buffer (gratuito):** máximo de 10 agendamentos pendentes por canal. Rate limits da API GraphQL.
- **TikTok:** alguns posts exigem confirmação no app (`ACTION_REQUIRED`).
- **Piper TTS:** local, gratuito, offline (quando voz pt-BR disponível).
- **NVIDIA NIM:** use apenas cota autorizada pelo usuário (`allow_nvidia_requests: true`).

## Não-publicação real

Nenhuma operação real contra Cloudinary, Buffer ou TikTok é executada durante o desenvolvimento. Todas as integrações externas usam mocks até que você forneça credenciais e autorize explicitamente via `live_enabled: true`.

## Fases do plano (Superpowers)

- **Fase A** — Brainstorming e requisitos (concluída)
- **Fase B** — Planejamento de implementação (docs/implementation-plan.md)
- **Fase C** — Desenvolvimento (em andamento; T1–T9 implementados)
- **Fase D** — Revisão (code-review-graph, Graphify)
- **Fase E** — Entrega (README, vídeos demo, verificação final)

## Licença

Uso interno e educacional. Respeite os termos de serviço do TikTok, Buffer e Cloudinary.