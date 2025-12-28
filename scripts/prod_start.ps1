#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Load-DotEnv([string]$EnvPath) {
    if (-not (Test-Path $EnvPath)) {
        throw ".env not found at $EnvPath"
    }

    Get-Content $EnvPath | ForEach-Object {
        $line = $_.Trim()
        if ($line.Length -eq 0) { return }
        if ($line.StartsWith("#")) { return }

        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { return }

        $key = $line.Substring(0, $idx).Trim()
        $val = $line.Substring($idx + 1).Trim()

        # 去掉可选的引号
        if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
            $val = $val.Substring(1, $val.Length - 2)
        }

        Set-Item -Path ("Env:{0}" -f $key) -Value $val
    }
}

function Ensure-Dir([string]$Path) {
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
    }
}

function Start-ServiceProcess {
    param(
        [Parameter(Mandatory=$true)][string]$Name,
        [Parameter(Mandatory=$true)][string]$WorkDir,
        [Parameter(Mandatory=$true)][string]$Args,
        [Parameter(Mandatory=$true)][string]$StdoutLog,
        [Parameter(Mandatory=$true)][string]$StderrLog,
        [Parameter(Mandatory=$true)][string]$PidFile
    )

    Write-Host "Starting $Name ..."
    Ensure-Dir (Split-Path -Parent $StdoutLog)
    Ensure-Dir (Split-Path -Parent $PidFile)

    # 用 python -m uvicorn，避免 uvicorn.exe 不在 PATH
    $python = (Get-Command python).Source

    $p = Start-Process `
        -FilePath $python `
        -ArgumentList $Args `
        -WorkingDirectory $WorkDir `
        -NoNewWindow `
        -PassThru `
        -RedirectStandardOutput $StdoutLog `
        -RedirectStandardError $StderrLog

    $p.Id | Set-Content -Encoding ASCII -Path $PidFile
    Write-Host "  -> PID $($p.Id) (logs: $StdoutLog)"
}

# ===== main =====
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$EnvPath  = Join-Path $RepoRoot ".env"

Write-Host "RepoRoot: $RepoRoot"
Load-DotEnv $EnvPath

$logsDir = Join-Path $RepoRoot "logs"
$pidsDir = Join-Path $RepoRoot ".pids"
Ensure-Dir $logsDir
Ensure-Dir $pidsDir

# 端口（你当前使用的）
$PORT_VOCAB    = 8000
$PORT_SPEAKING = 8001
$PORT_WRITING  = 8003
$PORT_GATEWAY  = 9000

# WorkDir（每个服务的目录）
$wdVocab    = Join-Path $RepoRoot "vocab_service"
$wdSpeaking = Join-Path $RepoRoot "speaking_service"
$wdWriting  = Join-Path $RepoRoot "streamlit_wrt_app"
$wdGateway  = $RepoRoot  # gateway 在 repoRoot/gateway/app.py

# 注意：prod 启动不带 --reload
Start-ServiceProcess `
    -Name "vocab_service" `
    -WorkDir $wdVocab `
    -Args "-m uvicorn app:app --host 127.0.0.1 --port $PORT_VOCAB" `
    -StdoutLog (Join-Path $logsDir "vocab.out.log") `
    -StderrLog (Join-Path $logsDir "vocab.err.log") `
    -PidFile (Join-Path $pidsDir "vocab.pid")

Start-ServiceProcess `
    -Name "speaking_service" `
    -WorkDir $wdSpeaking `
    -Args "-m uvicorn app:app --host 127.0.0.1 --port $PORT_SPEAKING" `
    -StdoutLog (Join-Path $logsDir "speaking.out.log") `
    -StderrLog (Join-Path $logsDir "speaking.err.log") `
    -PidFile (Join-Path $pidsDir "speaking.pid")

Start-ServiceProcess `
    -Name "writing_service" `
    -WorkDir $wdWriting `
    -Args "-m uvicorn app:app --host 127.0.0.1 --port $PORT_WRITING" `
    -StdoutLog (Join-Path $logsDir "writing.out.log") `
    -StderrLog (Join-Path $logsDir "writing.err.log") `
    -PidFile (Join-Path $pidsDir "writing.pid")

Start-ServiceProcess `
    -Name "gateway" `
    -WorkDir $wdGateway `
    -Args "-m uvicorn gateway.app:app --host 127.0.0.1 --port $PORT_GATEWAY" `
    -StdoutLog (Join-Path $logsDir "gateway.out.log") `
    -StderrLog (Join-Path $logsDir "gateway.err.log") `
    -PidFile (Join-Path $pidsDir "gateway.pid")

Write-Host ""
Write-Host "Started. Health checks:"
Write-Host "  writing  http://127.0.0.1:$PORT_WRITING/health"
Write-Host "  vocab    http://127.0.0.1:$PORT_VOCAB/health"
Write-Host "  speaking http://127.0.0.1:$PORT_SPEAKING/health"
Write-Host "  gateway  http://127.0.0.1:$PORT_GATEWAY/health"
Write-Host ""
Write-Host "Gateway routes example:"
Write-Host "  http://127.0.0.1:$PORT_GATEWAY/writing/health"
Write-Host "  http://127.0.0.1:$PORT_GATEWAY/vocab/health"
Write-Host "  http://127.0.0.1:$PORT_GATEWAY/speaking/health"