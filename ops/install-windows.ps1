<#
.SYNOPSIS
    Registra (nao ativa por padrao) a tarefa do Agendador de Tarefas do Windows
    para o pipeline TikTok Automation.

.DESCRIPTION
    Cria a tarefa "TikTokAutomation" que executa `python main.py tick` usando o
    interpretador do .venv do projeto, no diretorio de trabalho raiz.

    Por DEFAULT a tarefa eh deixada DESABILITADA (nenhum trabalho eh disparado
    e nenhuma instalacao eh feita automaticamente). Use -Enable para
    habilitar/ativar a partir de agora (opt-in).

    A tarefa roda como o usuario atual NAO-elevado, em sessao INTERATIVA, para
    que o HyperFrames/Chrome tenha acesso a sessao do usuario.
    REQUISITO DOCUMENTADO: o usuario deve estar ativamente logado; caso
    contrario a acao interativa nao funcionara (Chrome nao abre em sessao nao
    interativa).

    O fuso horario/horario de publicacao continua sendo decidido pelo Python
    (main.py tick via zoneinfo), nao pelo Agendador de Tarefas.

.PARAMETER TaskName
    Nome da tarefa no Agendador de Tarefas (padrao: TikTokAutomation).
.PARAMETER Root
    Diretorio raiz do projeto (padrao: pasta pai de ops/, resolvida em absoluto).
.PARAMETER TickMinutes
    Intervalo de repeticao da tarefa em minutos (padrao: 15). O proprio
    main.py tick decide se ha trabalho a fazer a cada disparo.
.PARAMETER Enable
    Habilita a tarefa apos registra-la (opt-in). Sem ela a tarefa fica
    desabilitada.
.PARAMETER Force
    Forca a sobrescricao se a tarefa ja existir. Sem ela, uma tarefa existente
    eh mantida intocada (nao sobrescrita silenciosamente).

.EXAMPLE
    .\ops\install-windows.ps1
    # Registra e deixa desabilitada.

.EXAMPLE
    .\ops\install-windows.ps1 -Enable
    # Registra e habilita a tarefa (apenas apos revisao/opt-in).

.EXAMPLE
    .\ops\install-windows.ps1 -TaskName TikTokAutomation -Force -Enable
    # Substitui uma tarefa existente e habilita.
#>
[CmdletBinding()]
param(
    [string]$TaskName = "TikTokAutomation",
    [string]$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [int]$TickMinutes = 15,
    [switch]$Enable,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path $Root
$PythonExe = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -Path $PythonExe -PathType Leaf)) {
    # Fallback apenas se nao houver .venv; em producao use o .venv absoluto.
    $PythonExe = "python"
    Write-Warning "Nenhum .venv\Scripts\python.exe encontrado em '$Root'; usando 'python' do PATH."
}
$MainFile = Join-Path $Root "main.py"
$LogFile = Join-Path $Root "logs\scheduler.log"

# Nao sobrescreve silenciosamente uma tarefa existente.
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    if (-not $Force) {
        Write-Warning "A tarefa '$TaskName' ja existe e sera mantida intocada (nao sobrescrita). Use -Force para substituir."
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
}

# Argumento com aspas adequadas para caminhos com espacos.
$arguments = "`"$MainFile`" tick"

$action = New-ScheduledTaskAction -Execute $PythonExe -Argument $arguments -WorkingDirectory $Root

# Execucao so quando o usuario esta logado (interativa) — necessario para Chrome/HyperFrames.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

# Configuracoes: ignora multiplas instancias e restricoes de energia apropriadas.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries:$false `
    -DontStartIfOnBatteries:$true `
    -StartWhenAvailable:$false `
    -RunOnlyIfIdle:$false `
    -WakeToRun:$false `
    -MultipleInstances:IgnoreNew `
    -DisallowHardTerminate:$true

# Gatilho: repete a cada TickMinutes por 24h (main.py tick decide se ha trabalho).
$trigger = New-ScheduledTaskTrigger -Daily -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes $TickMinutes) `
    -RepetitionDuration (New-TimeSpan -Hours 24)

$task = New-ScheduledTask -Action $action -Principal $principal -Settings $settings -Trigger $trigger

Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force:$Force | Out-Null

if (-not $Enable) {
    Disable-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Host "Tarefa '$TaskName' registrada e DESABILITADA. Nenhum trabalho disparado. Use -Enable para ativar."
} else {
    Enable-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Host "Tarefa '$TaskName' registrada e HABILITADA."
}

Write-Host @"
Notas de instalacao:
- Interpretador: "$PythonExe" (caminho absoluto; nao depende de PATH nem do perfil do usuario).
- Diretorio de trabalho: "$Root".
- Log de saida: "$LogFile" (garanta que a pasta logs/ exista e seja gravavel).
- Usuario: "$env:USERNAME" (nao-elevado, interativo). O usuario deve estar ativamente logado para que o HyperFrames/Chrome funcione.
- Fuso/horario: a decisao de quando publicar fica no Python (main.py tick), nao no Agendador de Tarefas.
- Para desabilitar: Disable-ScheduledTask -TaskName '$TaskName'
- Para remover:   Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:'$false'
"@
