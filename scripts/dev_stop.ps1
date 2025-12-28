# scripts/dev_stop.ps1
# Stop EduTech local services by killing processes listening on target ports.
# Works even if netstat / taskkill are not in PATH.

$ErrorActionPreference = "Stop"

Write-Host "Stopping EduTech local services..."

$ports = @(8000, 8001, 8003, 9000)

$netstat = Join-Path $env:WINDIR "System32\netstat.exe"
$taskkill = Join-Path $env:WINDIR "System32\taskkill.exe"

function Get-PidsByPort([int]$port) {
    if (-not (Test-Path $netstat)) {
        throw "netstat not found at: $netstat"
    }

    # netstat -ano output includes lines like:
    # TCP    127.0.0.1:8000   0.0.0.0:0   LISTENING   12345
    $lines = & $netstat -ano 2>$null

    # Use SIMPLE string match, no regex needed.
    $needle = ":" + $port + " "

    $pids = New-Object System.Collections.Generic.HashSet[int]

    foreach ($line in $lines) {
        if ($line -like "*$needle*") {
            $parts = ($line -split "\s+") | Where-Object { $_ -ne "" }
            if ($parts.Count -ge 5) {
                $targetPid = $parts[-1]
                if ($targetPid -match "^\d+$") {
                    [void]$pids.Add([int]$targetPid)
                }
            }
        }
    }

    return $pids
}

foreach ($port in $ports) {
    try {
        $pids = Get-PidsByPort -port $port

        if ($pids.Count -eq 0) {
            Write-Host "Port $port not in use"
            continue
        }

        foreach ($targetPid in $pids) {
            Write-Host "Killing PID $targetPid (port $port)"
            if (Test-Path $taskkill) {
                & $taskkill /PID $targetPid /T /F | Out-Null
            } else {
                Stop-Process -Id $targetPid -Force
            }
        }
    }
    catch {
        # IMPORTANT: use ${port} to avoid the colon parsing issue you hit before
        Write-Host "Failed on port ${port}: $($_.Exception.Message)"
    }
}

Write-Host "All target ports processed."