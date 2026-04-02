param(
    [int]$IntervalSeconds = 60
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Error "Missing .venv python: $pythonExe"
    exit 2
}

$runtimeDir = Join-Path $repoRoot "runtime"
$logsDir = Join-Path $runtimeDir "logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

$pidPath = Join-Path $runtimeDir "specstory_live_loop.pid"
if (Test-Path $pidPath) {
    $oldPid = Get-Content -Raw $pidPath
    if ($oldPid -match '^\d+$') {
        $running = Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue
        if ($running) {
            Write-Host "[specstory-live] already running PID=$oldPid"
            exit 0
        }
    }
}

$out = Join-Path $logsDir "specstory_live_loop.out.log"
$err = Join-Path $logsDir "specstory_live_loop.err.log"

$pyArgs = @(
    "scripts/specstory_live_loop.py",
    "--interval-seconds", "$IntervalSeconds"
)

$proc = Start-Process -FilePath $pythonExe `
    -ArgumentList $pyArgs `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $out `
    -RedirectStandardError $err `
    -PassThru

Set-Content -Path $pidPath -Value "$($proc.Id)" -Encoding ascii
Write-Host "[specstory-live] started PID=$($proc.Id), interval=${IntervalSeconds}s"
