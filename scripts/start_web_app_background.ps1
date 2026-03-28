param(
    [switch]$SkipFrontendBuild
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Error "Missing .venv python: $pythonExe"
    exit 2
}

function Test-PortListening {
    param([int]$Port)
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        return $null -ne $conn
    } catch {
        return $false
    }
}

if (Test-PortListening -Port 8012) {
    Write-Host "[start_web_app_background] Port 8012 already listening, skip."
    exit 0
}

if (-not $SkipFrontendBuild) {
    $distIndex = Join-Path $repoRoot "frontend\dist\index.html"
    if (-not (Test-Path $distIndex)) {
        Write-Host "[start_web_app_background] frontend/dist missing, running build."
        npm --prefix frontend run build
    } else {
        Write-Host "[start_web_app_background] frontend/dist present, skip build."
    }
} else {
    Write-Host "[start_web_app_background] Frontend build skipped by flag."
}

$logDir = Join-Path $repoRoot "runtime\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$out = Join-Path $logDir "backend.out.log"
$err = Join-Path $logDir "backend.err.log"

$proc = Start-Process -FilePath $pythonExe `
    -ArgumentList @("-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8012") `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $out `
    -RedirectStandardError $err `
    -PassThru

Write-Host "[start_web_app_background] Started PID=$($proc.Id)"
