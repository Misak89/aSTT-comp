$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$pidPath = Join-Path $repoRoot "runtime\specstory_live_loop.pid"
if (-not (Test-Path $pidPath)) {
    Write-Host "[specstory-live] pid file not found"
    exit 0
}

$pidRaw = (Get-Content -Raw $pidPath).Trim()
if (-not ($pidRaw -match '^\d+$')) {
    Write-Host "[specstory-live] invalid pid file content: $pidRaw"
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    exit 0
}

$loopPid = [int]$pidRaw
$proc = Get-Process -Id $loopPid -ErrorAction SilentlyContinue
if ($null -ne $proc) {
    Stop-Process -Id $loopPid -Force
    Write-Host "[specstory-live] stopped PID=$loopPid"
} else {
    Write-Host "[specstory-live] process PID=$loopPid not running"
}

Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
