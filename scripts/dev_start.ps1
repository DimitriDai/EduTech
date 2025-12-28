Write-Host "Starting EduTech local services..."

$ROOT = Split-Path -Parent $PSScriptRoot

# ---------- 1. 读取 .env ----------
$envFile = Join-Path $ROOT ".env"
if (!(Test-Path $envFile)) {
    Write-Error ".env not found at $envFile"
    exit 1
}

Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*#') { return }
    if ($_ -match '^\s*$') { return }
    $parts = $_ -split '=', 2
    if ($parts.Length -eq 2) {
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

# ---------- 2. vocab ----------
Start-Process powershell `
  -ArgumentList @(
    "-NoExit",
    "-Command",
    "cd '$ROOT\vocab_service'; python -m uvicorn app:app --reload --port 8000"
  )

# ---------- 3. speaking ----------
Start-Process powershell `
  -ArgumentList @(
    "-NoExit",
    "-Command",
    "cd '$ROOT\speaking_service'; python -m uvicorn app:app --reload --port 8001"
  )

# ---------- 4. writing ----------
Start-Process powershell `
  -ArgumentList @(
    "-NoExit",
    "-Command",
    "cd '$ROOT\streamlit_wrt_app'; python -m uvicorn app:app --reload --port 8003"
  )

# ---------- 5. gateway ----------
Start-Process powershell `
  -ArgumentList @(
    "-NoExit",
    "-Command",
    "cd '$ROOT'; python -m uvicorn gateway.app:app --reload --port 9000"
  )

Write-Host "All services launched."
Write-Host "Gateway: http://127.0.0.1:9000"