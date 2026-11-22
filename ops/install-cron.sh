#!/usr/bin/env bash
#
# ops/install-cron.sh
#
# Imprime uma entrada de cron para o TikTok Automation SEM alterar crontabs
# existentes. O operador deve adicionar a linha ao crontab manualmente
# (crontab -e). Instrucoes claras de desabilitacao sao impressas abaixo.
#
# Nao depende do VS Code. Usa o interpretador absoluto do .venv quando
# disponivel; o fuso horario/horario de publicacao eh decidido pelo Python
# (zoneinfo), nao pelo cron.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
    VENV_PYTHON="python3"
    echo "Aviso: .venv/bin/python nao encontrado; usando 'python3' do PATH." >&2
fi
MAIN="$ROOT/main.py"
LOG="$ROOT/logs/cron.log"
TICK_MINUTES="${TICK_MINUTES:-15}"

cat <<EOF
# =============================================================================
# TikTok Automation — agendamento de publicacao (cron)
# Adicione a linha abaixo ao crontab do usuario:  crontab -e
# Esta entrada NAO eh gravada automaticamente (crontabs existentes nao sao alterados).
# =============================================================================
# Roda a cada ${TICK_MINUTES} minutos; o proprio main.py decide se ha trabalho.
# Diretorio de trabalho definido explicitamente para a raiz do projeto.
*/${TICK_MINUTES} * * * * cd "$ROOT" && "$VENV_PYTHON" "$MAIN" tick >> "$LOG" 2>&1
# =============================================================================

COMO DESABILITAR / REMOVER:
  # Abra o crontab e comente ou remova a linha acima:
    crontab -e

  # Ou remova todas as entradas do TikTok Automation de uma vez:
    crontab -l | grep -v 'main.py tick' | crontab -

  # Verifique que nao restou nenhuma entrada:
    crontab -l | grep -i tiktok || true

NOTAS:
- Interpretador absoluto: "$VENV_PYTHON" (nao depende do PATH nem do perfil do usuario).
- Diretorio de trabalho: "$ROOT" (definido com 'cd' na propria linha do cron).
- Log de saida: "$LOG" (garanta que a pasta logs/ exista e seja gravavel).
- Fuso/horario: a decisao de quando publicar fica no Python (main.py tick via zoneinfo), nao no cron.
EOF
