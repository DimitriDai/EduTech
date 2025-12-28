#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

function Try-StopByPidFile([string]$PidFile) {
    if (-not (Test-Path $PidFile)) { return $false }

    $pidText = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $pidText) { return $false }

    $pidNum = 0
    if (-not [int]::TryParse($pidText.Trim(), [ref]$pidNum)) { return $false }

    try {
        $p = Get-Process -Id $pidNum -ErrorAction Stop
        Write-Host "Killing PID $pidNum (from $([IO.Path]::GetFileName($PidFile)))"
        Stop-Process -Id $pidNum -Force
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        return $true
    } catch {
        # 进程可能已不存在
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
        return $false
    }
}

function Get-PidsByPort([int]$Port) {
    # 用系统自带 netstat.exe（避免 PATH 问题）
    $netstat = Join-Path $env:WINDIR "System32\netstat.exe"
    if (-not (Test-Path $netstat)) { return @() }

    $lines = & $netstat -ano | Select-String -SimpleMatch ":$Port"
    $pids = @()

    foreach ($m in $lines) {
        $parts = ($m.Line -split "\s+") | Where-Object { $_ -ne "" }
        # netstat 常见格式：Proto LocalAddress ForeignAddress State PID
        if ($parts.Count -ge 5) {
            $pid = $parts[-1]
            $pidNum = 0
            if ([int]::TryParse($pid, [ref]$pidNum)) {
                $pids += $pidNum
            }
        }
    }

    $pids | Sort-Object -Unique
}

function Stop-ByPort([int]$Port) {
    $pids = Get-PidsByPort $Port
    if (-not $pids -or $pids.Count -eq 0) {
        Write-Host "Port $Port not in use"
        return
    }

    foreach ($pidNum in $pids) {
        try {
            Write-Host "Killing PID $pidNum (port $Port)"
            Stop-Process -Id $pidNum -Force -ErrorAction Stop
        } catch {
            Write-Host "  Failed to kill PID $pidNum : $($_.Exception.Message)"
        }
    }
}

# ===== main =====
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pidsDir  = Join-Path $RepoRoot ".pids"

Write-Host "Stopping EduTech local services..."

# 先按 pidfile 停（最准确）
$stopped = $false
$pidFiles = @("gateway.pid","writing.pid","speaking.pid","vocab.pid") | ForEach-Object { Join-Path $pidsDir $_ }

foreach ($pf in $pidFiles) {
    if (Try-StopByPidFile $pf) { $stopped = $true }
}

# 如果没有 pidfile 或失败，再按端口兜底
# 端口（你当前使用的）
$ports = @(8000, 8001, 8003, 9000)
foreach ($port in $ports) {
    Stop-ByPort $port
}

Write-Host "All target ports processed."