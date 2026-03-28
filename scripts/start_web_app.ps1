param(
    [switch]$SkipFrontendBuild,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$pythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    Write-Error "Missing .venv python: $pythonExe"
    exit 2
}

function Test-FrontendBuildNeeded {
    $distIndex = Join-Path $repoRoot "frontend\dist\index.html"
    if (-not (Test-Path $distIndex)) { return $true }

    $distTime = (Get-Item $distIndex).LastWriteTimeUtc
    $srcDirs = @(
        (Join-Path $repoRoot "frontend\src"),
        (Join-Path $repoRoot "frontend\index.html"),
        (Join-Path $repoRoot "frontend\vite.config.ts"),
        (Join-Path $repoRoot "frontend\package.json")
    )

    foreach ($p in $srcDirs) {
        if (-not (Test-Path $p)) { continue }
        if ((Get-Item $p).PSIsContainer) {
            $newer = Get-ChildItem -Path $p -Recurse -File -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTimeUtc -gt $distTime } |
                Select-Object -First 1
            if ($newer) { return $true }
        } else {
            if ((Get-Item $p).LastWriteTimeUtc -gt $distTime) { return $true }
        }
    }
    return $false
}

if (-not $SkipFrontendBuild) {
    if (Test-FrontendBuildNeeded) {
        Write-Host "[start_web_app] Frontend build needed -> npm --prefix frontend run build"
        npm --prefix frontend run build
    } else {
        Write-Host "[start_web_app] Frontend dist is up to date."
    }
} else {
    Write-Host "[start_web_app] Frontend build skipped by flag."
}

$reloadFlag = if ($Reload) { "--reload" } else { "" }
Write-Host "[start_web_app] Starting backend on http://127.0.0.1:8012"
if ($Reload) {
    & $pythonExe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8012 --reload
} else {
    & $pythonExe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8012
}
