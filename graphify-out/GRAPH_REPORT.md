# Graph Report - tiktok-automation  (2026-11-20)

## Corpus Check
- Corpus is ~20,151 words - fits in a single context window. You may not need a graph.

## Summary
- 490 nodes · 1232 edges · 21 communities (12 shown, 9 thin omitted)
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 145 edges (avg confidence: 0.9)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Roteiros e fontes (scripts/ideias)
- CLI e orquestrador (main.py + orchestrator)
- Banco de dados e maquina de estados
- Provedores LLM e integroes (ai + integrations)
- Configuracao e settings (config.yaml + .env)
- Clientes Cloudinary e Buffer
- Video: TTS Piper e validador FFprobe
- Configuracao yaml (nodes de config)
- Agendador e fuso horario (scheduler)
- Documentacao e plano Superpowers
- Provedores LLM Assisted/NVIDIA
- Renderer HTML/CSS para MP4
- Fontes de configuracao (sources.yaml)
- Ops: cron (install-cron.sh)
- Templates curiosidade e explicacao
- Templates lista e QA
- Requisitos runtime (requirements.txt)
- Template historia (story)

## God Nodes (most connected - your core abstractions)
1. `PipelineError` - 98 edges
2. `Orchestrator` - 42 edges
3. `Database` - 32 edges
4. `load_script()` - 29 edges
5. `load_settings()` - 24 edges
6. `BufferClient` - 19 edges
7. `StateMachine` - 19 edges
8. `_catalog()` - 19 edges
9. `DuplicateError` - 17 edges
10. `CloudinaryClient` - 17 edges

## Surprising Connections (you probably didn't know these)
- `test_assisted_provider_rejects_invalid_json_response()` --uses--> `PipelineError`  [INFERRED]
  tests/test_ai_provider.py → src/errors.py
- `test_assisted_provider_writes_prompt_and_reads_response()` --uses--> `PipelineError`  [INFERRED]
  tests/test_ai_provider.py → src/errors.py
- `test_build_provider_unknown()` --uses--> `PipelineError`  [INFERRED]
  tests/test_ai_provider.py → src/errors.py
- `test_nvidia_provider_requires_api_key()` --uses--> `PipelineError`  [INFERRED]
  tests/test_ai_provider.py → src/errors.py
- `test_buffer_mock_requires_channel_id()` --uses--> `PipelineError`  [INFERRED]
  tests/test_integrations_mock.py → src/errors.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **HTML video template system** — templates_curiosity_template, templates_explainer_template, templates_list_template, templates_qa_template, templates_story_template [INFERRED 0.85]
- **Free-tier external services with usage limits** — readme_cloudinary, readme_buffer, readme_tiktok [INFERRED 0.85]

## Communities (21 total, 9 thin omitted)

### Community 0 - "Roteiros e fontes (scripts/ideias)"
Cohesion: 0.07
Nodes (40): build_prompt(), idea_prompt(), load_script(), normalize_topic(), _parse_source(), read_catalog(), _require_str(), _resolve_sources() (+32 more)

### Community 1 - "CLI e orquestrador (main.py + orchestrator)"
Cohesion: 0.09
Nodes (18): _build_parser(), cmd_approve(), cmd_generate(), cmd_review(), cmd_run_daily(), cmd_schedule(), cmd_status(), main() (+10 more)

### Community 2 - "Banco de dados e maquina de estados"
Cohesion: 0.09
Nodes (9): Database, _utc_now(), DuplicateError, StateMachine, test_create_and_transition_video(), test_database_creates_tables(), test_record_error_and_clear(), test_scheduled_post_upsert_idempotent() (+1 more)

### Community 3 - "Provedores LLM e integroes (ai + integrations)"
Cohesion: 0.09
Nodes (3): _extract_json(), _json_dump(), _json_load()

### Community 4 - "Configuracao e settings (config.yaml + .env)"
Cohesion: 0.13
Nodes (19): CloudinarySettings, ConfigurationError, LLMSettings, _load_raw_config(), load_settings(), _parse_time(), _parse_weekdays(), PublicationSettings (+11 more)

### Community 5 - "Clientes Cloudinary e Buffer"
Cohesion: 0.09
Nodes (10): BufferClient, PostResult, CloudinaryClient, UploadResult, test_buffer_mock_create_post_is_idempotent(), test_buffer_mock_list_pending_returns_scheduled(), test_buffer_mock_requires_channel_id(), test_cloudinary_mock_check_quota() (+2 more)

### Community 6 - "Video: TTS Piper e validador FFprobe"
Cohesion: 0.09
Nodes (9): AudioSegment, PiperTTS, FFprobeValidator, VideoInfo, test_tts_fallback_writes_silence_wav(), test_tts_is_available_false_without_model(), test_validate_tiktok_rejects_missing_file(), test_validator_raises_when_ffprobe_missing() (+1 more)

### Community 7 - "Configuracao yaml (nodes de config)"
Cohesion: 0.06
Nodes (35): auto_enabled, cloudinary, max_video_mb, stop_usage_percent, live_enabled, llm, allow_nvidia_requests, provider (+27 more)

### Community 8 - "Agendador e fuso horario (scheduler)"
Cohesion: 0.17
Nodes (20): _local_date(), next_slot(), _to_utc(), _tz(), valid_production_day(), _make_settings(), _sp(), test_next_slot_avoids_duplicate_of_occupied_publish_time() (+12 more)

### Community 9 - "Documentacao e plano Superpowers"
Cohesion: 0.08
Nodes (30): Implementation plan (Superpowers Fases A–E), Fase A — Brainstorming (concluded), Fase B — Implementation planning (structure + T1–T15 tasks), Fase C — Development (TDD cycle), Fase D — Review (code-review-graph), Fase E — Delivery, 11 measurable success criteria (v1 acceptance), Tasks T1–T15 with dependencies and acceptance criteria (+22 more)

### Community 10 - "Provedores LLM Assisted/NVIDIA"
Cohesion: 0.13
Nodes (9): AssistedProvider, build_provider(), NVIDIAProvider, LLMProvider, test_assisted_provider_rejects_invalid_json_response(), test_assisted_provider_writes_prompt_and_reads_response(), test_build_provider_assisted_defaults(), test_build_provider_unknown() (+1 more)

### Community 12 - "Fontes de configuracao (sources.yaml)"
Cohesion: 0.29
Nodes (6): cern-first-website, accessed_at, editorial_evidence, publisher, title, url

## Knowledge Gaps
- **23 isolated node(s):** `PUBLISHED terminal state`, `FAILED terminal state (resumable)`, `config.yaml configuration (modes, limits, video, publication, cloudinary)`, `.env environment variables (CLOUDINARY_*, BUFFER_*, NVIDIA_*)`, `Windows Task Scheduler / cron scheduling (TikTokAutomation task, main.py tick every 15 min)` (+18 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 188 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **9 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PipelineError` connect `Community 1` to `Community 0`, `Community 2`, `Community 3`, `Community 5`, `Community 6`, `Community 10`, `Community 11`?**
  _High betweenness centrality (0.286) - this node is a cross-community bridge._
- **Are the 37 inferred relationships involving `PipelineError` (e.g. with `cmd_approve()` and `cmd_generate()`) actually correct?**
  _`PipelineError` has 37 INFERRED edges - model-reasoned connections that need verification._
- **What connects `PUBLISHED terminal state`, `FAILED terminal state (resumable)`, `config.yaml configuration (modes, limits, video, publication, cloudinary)` to the rest of the system?**
  _23 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.07023705004389816 - nodes in this community are weakly interconnected._
- **Why does `Database` connect `Community 2` to `Community 1`, `Community 3`, `Community 6`?**
  _High betweenness centrality (0.060) - this node is a cross-community bridge._
- **Are the 16 inferred relationships involving `Orchestrator` (e.g. with `cmd_approve()` and `cmd_generate()`) actually correct?**
  _`Orchestrator` has 16 INFERRED edges - model-reasoned connections that need verification._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.09427609427609428 - nodes in this community are weakly interconnected._