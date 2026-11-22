export const meta = {
  name: 'cp13-priority2-quality-gates',
  description: 'Quality Gates configuráveis (blackdetect/silencedetect/cenas dinâmicas/áudio) + documentação HyperFrames',
  phases: [
    { title: 'Design', detail: 'Definir limites configuráveis e verificação de cenas dinâmicas no validator.py' },
    { title: 'Implement', detail: 'Refatorar validator.py com limites e detecção de cenas dinâmicas' },
    { title: 'Testes', detail: 'Escrever testes de regressão dos Quality Gates' },
    { title: 'Documentação', detail: 'Documentar bloqueio HyperFrames e Quality Gates no README/config' },
    { title: 'Regressão', detail: 'Executar suíte completa pytest e confirmar zero regressões' },
  ],
};

// Schema do output da fase Design
const DESIGN_SCHEMA = {
  type: 'object',
  properties: {
    max_black_ratio: { type: 'number', description: 'Fração máxima de frames pretos permitida (0-1)' },
    max_continuous_black_seconds: { type: 'number', description: 'Duração máxima contínua de preto em segundos' },
    min_mean_volume_db: { type: 'number', description: 'Volume médio mínimo em dB para áudio audível' },
    scene_change_min_frames: { type: 'number', description: 'Número mínimo de frames diferentes para considerar cena dinâmica' },
    approach: { type: 'string', description: 'Resumo da abordagem escolhida' },
  },
  required: ['max_black_ratio', 'max_continuous_black_seconds', 'min_mean_volume_db', 'approach'],
};

// Schema do output da fase Implement
const IMPLEMENT_SCHEMA = {
  type: 'object',
  properties: {
    files_changed: { type: 'array', items: { type: 'string' }, description: 'Arquivos modificados' },
    summary: { type: 'string', description: 'Resumo das alterações' },
  },
  required: ['files_changed', 'summary'],
};

// Schema do output da fase Testes
const TESTES_SCHEMA = {
  type: 'object',
  properties: {
    test_file: { type: 'string', description: 'Arquivo de teste criado' },
    tests_added: { type: 'number', description: 'Quantidade de testes adicionados' },
    summary: { type: 'string', description: 'Resumo dos testes' },
  },
  required: ['test_file', 'tests_added', 'summary'],
};

// Schema do output da fase Documentação
const DOCS_SCHEMA = {
  type: 'object',
  properties: {
    files_changed: { type: 'array', items: { type: 'string' }, description: 'Arquivos de documentação modificados' },
    summary: { type: 'string', description: 'Resumo da documentação' },
  },
  required: ['files_changed', 'summary'],
};

// Schema do output da fase Regressão
const REGRESSAO_SCHEMA = {
  type: 'object',
  properties: {
    total_passed: { type: 'number' },
    total_failed: { type: 'number' },
    regressions: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
  },
  required: ['total_passed', 'total_failed', 'regressions', 'summary'],
};

phase('Design');
const design = await agent(`
Você é arquiteto de software. Leia o arquivo src/video/validator.py (especialmente FFprobeValidator._assert_not_degenerate) e src/workflow/graph.py (quality_agent) no repositório d:\\Videos-IA\\tiktok-automation.

O problema atual: _assert_not_degenerate rejeita QUALQUER frame preto detectado pelo blackdetect (mesmo 1 quadro) e qualquer áudio com mean_volume <= -60 dB. Isso é muito rígido e rejeitaria transições pretas intencionais ou pausas curtas de áudio.

Proponha um design de Quality Gates configuráveis que:
1. Torne os limites configuráveis (não hardcode): max_black_ratio (fração de frames pretos tolerada), max_continuous_black_seconds (preto contínuo máximo), min_mean_volume_db (volume mínimo audível), scene_change_min_frames.
2. Mantenha strict=False permissivo (validação técnica apenas) e strict=True aplicando os limites de degeneração de forma razoável.
3. Adicione verificação de cenas dinâmicas: o vídeo não pode ser estático (mesmo frame repetido por toda duração).
4. Adicione verificação de áudio audível com limite configurável.
5. Mantenha compatibilidade com os testes existentes (test_quality_strict_rejects_black_silent_video, test_quality_agent_validates_real_mp4).

Retorne apenas o JSON do design com os campos do schema DESIGN_SCHEMA. Não explique fora do JSON.
`, { schema: DESIGN_SCHEMA, phase: 'Design' });

phase('Implement');
const implement = await agent(`
Você é desenvolvedor Python. No repositório d:\\Videos-IA\\tiktok-automation, refatore src/video/validator.py para implementar Quality Gates configuráveis com base no design aprovado.

Requisitos:
- Tornar os limites configuráveis via parâmetros de validate_tiktok (e/ou construtor): max_black_ratio, max_continuous_black_seconds, min_mean_volume_db, com defaults sensíveis.
- Em strict=True, aplicar: (a) fração de frames pretos <= max_black_ratio, (b) preto contínuo <= max_continuous_black_seconds, (c) mean_volume >= min_mean_volume_db (ou ausência de áudio = erro quando require_audio).
- Adicionar verificação de cenas dinâmicas: extrair alguns frames ao longo do vídeo e confirmar que não são todos idênticos (vídeo estático). Usar FFmpeg para gerar thumbnails e comparar, ou blackdetect/framepar. Se o vídeo for estático (mesmo conteúdo em todos os amostras), rejeitar em strict=True.
- Manter a assinatura pública compatível: validate_tiktok(path, *, width, height, fps, max_duration_seconds, require_audio, max_size_mb, strict) deve continuar funcionando. Adicionar novos parâmetros keyword-only com defaults.
- Não quebrar os testes existentes: test_quality_strict_rejects_black_silent_video (vídeo preto+silencioso deve falhar em strict=True) e test_quality_agent_validates_real_mp4 (MP4 real com áudio deve passar).

Retorne JSON com files_changed (lista de caminhos relativos) e summary (texto curto). Não explique fora do JSON.
`, { schema: IMPLEMENT_SCHEMA, phase: 'Implement' });

phase('Testes');
const testes = await agent(`
Você é desenvolvedor de testes (QA). No repositório d:\\Videos-IA\\tiktok-automation, crie/estenda testes de regressão para os Quality Gates configuráveis em src/video/validator.py.

Crie o arquivo tests/test_quality_gates.py com testes TDD (reproduzir defeito → corrigir → verificar) cobrindo:
1. Vídeo preto + silencioso é rejeitado em strict=True (já existe, manter compatível).
2. Vídeo com frames pretos dentro do limite (max_black_ratio) NÃO é rejeitado (transições pretas intencionais toleradas).
3. Áudio com volume abaixo de min_mean_volume_db é rejeitado em strict=True; áudio audível é aceito.
4. Vídeo estático (mesmo frame repetido) é rejeitado em strict=True (cenas dinâmicas).
5. Limites configuráveis funcionam (passar max_black_ratio, max_continuous_black_seconds, min_mean_volume_db como parâmetros).
6. strict=False permanece permissivo (não rejeita por degeneração).
7. Vídeo real (não preto, com áudio) passa em strict=True.

Use FFmpeg para gerar os MP4 de teste (preto, silencioso, com áudio, estático, real). Cada teste deve ser independente e usar tmp_path.
Retorne JSON com test_file (caminho relativo), tests_added (número) e summary. Não explique fora do JSON.
`, { schema: TESTES_SCHEMA, phase: 'Testes' });

phase('Documentação');
const docs = await agent(`
Você é redator técnico. No repositório d:\\Videos-IA\\tiktok-automation, atualize a documentação para refletir a conclusão da Prioridade 2 do CP13:

1. docs/implementation-plan.md: já menciona que HyperFrames não está disponível e que o renderer próprio (Node + headless Chrome + FFmpeg) é o fallback. Adicione uma seção curta "Prioridade 2 — Quality Gates" resumindo os limites configuráveis (blackdetect/silencedetect/cenas dinâmicas/áudio) e que a investigação HyperFrames concluiu por não haver pacote disponível localmente, com fallback documentado.
2. Se houver README.md na raiz, adicione uma seção "Quality Gates" e "Renderização real" resumindo dependências (FFmpeg, Chromium/Puppeteer) e os parâmetros configuráveis.
3. Se houver config.yaml, garanta que existe referência aos limites de qualidade (ou adicione exemplo).

Retorne JSON com files_changed e summary. Se um arquivo não existir, não o crie — apenas liste os que realmente modificou. Não explique fora do JSON.
`, { schema: DOCS_SCHEMA, phase: 'Documentação' });

phase('Regressão');
const regressao = await agent(`
No repositório d:\\Videos-IA\\tiktok-automation, execute a suíte completa de testes com pytest na .venv e relate o resultado.

Comando: .venv\\Scripts\\python -m pytest tests/ -v

Capture a contagem de passed/failed e liste qualquer teste que falhou. Retorne JSON com total_passed, total_failed, regressions (lista de nomes de testes falhados, vazio se nenhum) e summary (texto curto). Não explique fora do JSON.
`, { schema: REGRESSAO_SCHEMA, phase: 'Regressão' });

return {
  design,
  implement,
  testes,
  docs,
  regressao,
};
