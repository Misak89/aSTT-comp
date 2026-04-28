param(
  [string]$RepoRoot = "",
  [switch]$DryRun,
  [switch]$StrictPrereq
)

$ErrorActionPreference = "Stop"

function Write-Head([string]$Text) {
  Write-Host ""
  Write-Host "== $Text ==" -ForegroundColor Cyan
}

function Ask-YesNo([string]$Prompt, [bool]$DefaultYes = $true) {
  $suffix = if ($DefaultYes) { "[Y/n]" } else { "[y/N]" }
  while ($true) {
    $raw = Read-Host "$Prompt $suffix"
    if ([string]::IsNullOrWhiteSpace($raw)) { return $DefaultYes }
    $v = $raw.Trim().ToLowerInvariant()
    if ($v -in @("y", "yes", "a", "ano")) { return $true }
    if ($v -in @("n", "no", "ne")) { return $false }
    Write-Host "Zadej y nebo n." -ForegroundColor Yellow
  }
}

function Test-Cmd([string]$Name) {
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Ensure-Dir([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
  }
}

function Require-File([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) {
    throw "Chybi pozadovany soubor/adresar: $Path"
  }
}

function Resolve-RepoRoot([string]$InputRoot) {
  if ($InputRoot -and $InputRoot.Trim()) {
    return (Resolve-Path -LiteralPath $InputRoot).Path
  }
  $scriptDir = Split-Path -Parent $PSCommandPath
  return (Resolve-Path -LiteralPath (Join-Path $scriptDir "..")).Path
}

function Pick-Python([string]$Repo) {
  $venvPy = Join-Path $Repo ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPy) { return $venvPy }
  if (Test-Cmd "py") { return "py -3.13" }
  if (Test-Cmd "python") { return "python" }
  throw "Python nebyl nalezen. Nainstaluj Python 3.13+ a spust skript znovu."
}

function Test-RepoWritable([string]$Repo) {
  $runtime = Join-Path $Repo "runtime"
  $probeParent = if (Test-Path -LiteralPath $runtime) { $runtime } else { $Repo }
  $probe = Join-Path $probeParent (".astt_install_dry_run_" + [guid]::NewGuid().ToString("N") + ".tmp")
  Set-Content -LiteralPath $probe -Value "ok" -Encoding UTF8
  Remove-Item -LiteralPath $probe -Force
}

function Invoke-DryRunValidation([string]$Repo, [string[]]$MissingPrereq) {
  Write-Head "Dry-run validace instalace"
  Write-Host "Dry-run: nic se nestahuje, nic se neinstaluje." -ForegroundColor Yellow

  Require-File (Join-Path $Repo "backend\requirements.txt")
  Require-File (Join-Path $Repo "frontend\package.json")
  Require-File (Join-Path $Repo "frontend\package-lock.json")
  Require-File (Join-Path $Repo "scripts\webctl.py")
  Require-File (Join-Path $Repo "scripts\check_health.py")
  Require-File (Join-Path $Repo "scripts\portability_audit.py")
  Test-RepoWritable $Repo

  try {
    $py = Pick-Python $Repo
    Write-Host "OK  - python candidate: $py" -ForegroundColor Green
  } catch {
    Write-Host "MISS- python candidate: $($_.Exception.Message)" -ForegroundColor Yellow
    $MissingPrereq += "python"
  }

  Write-Host ""
  Write-Host "Planovane kroky plne instalace:" -ForegroundColor White
  Write-Host "  1. vytvorit/aktualizovat .venv"
  Write-Host "  2. nainstalovat backend/requirements.txt"
  Write-Host "  3. npm --prefix frontend install"
  Write-Host "  4. npm --prefix frontend run build"
  Write-Host "  5. pripravit whisper.cpp runtime"
  Write-Host "  6. stahnout vybrane modely do runtime/model_store"
  Write-Host "  7. overit scripts/check_health.py a scripts/check_model.py"

  if ($StrictPrereq -and $MissingPrereq.Count -gt 0) {
    Write-Host "Dry-run FAIL: chybi prerekvizity: $($MissingPrereq -join ', ')" -ForegroundColor Red
    return 1
  }
  Write-Host "Dry-run OK" -ForegroundColor Green
  return 0
}

function Invoke-Python([string]$PyCmd, [string]$Args) {
  if ($PyCmd -eq "py -3.13") {
    & py -3.13 $Args
  } else {
    & $PyCmd $Args
  }
}

function Download-FileWithPrompt(
  [string]$Label,
  [string]$ApproxSize,
  [string]$Url,
  [string]$TargetPath
) {
  Write-Host ""
  Write-Host "$Label ($ApproxSize)" -ForegroundColor White
  Write-Host "URL: $Url" -ForegroundColor DarkGray
  if (-not (Ask-YesNo "Stahnout tento balicek?" $true)) {
    Write-Host "Preskoceno: $Label" -ForegroundColor Yellow
    return $false
  }
  Ensure-Dir (Split-Path -Parent $TargetPath)
  Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $TargetPath
  $sizeMb = [math]::Round((Get-Item -LiteralPath $TargetPath).Length / 1MB, 1)
  Write-Host "Hotovo -> $TargetPath ($sizeMb MB)" -ForegroundColor Green
  return $true
}

function Install-WhisperRuntime([string]$Repo) {
  $runtimeTarget = Join-Path $Repo "runtime\model_store\whisper_cpp"
  Ensure-Dir $runtimeTarget

  $custom = Read-Host "Cesta k whisper-cli(.exe) nebo adresari s whisper-cli/whisper-server (Enter = automaticky download)"
  if ($custom -and $custom.Trim()) {
    $customPath = (Resolve-Path -LiteralPath $custom).Path
    if ((Get-Item -LiteralPath $customPath).PSIsContainer) {
      $cli = Get-ChildItem -Path $customPath -Recurse -Filter "whisper-cli*.exe" | Select-Object -First 1
      $srv = Get-ChildItem -Path $customPath -Recurse -Filter "whisper-server*.exe" | Select-Object -First 1
      if ($cli) { Copy-Item -LiteralPath $cli.FullName -Destination (Join-Path $runtimeTarget "whisper-cli.exe") -Force }
      if ($srv) { Copy-Item -LiteralPath $srv.FullName -Destination (Join-Path $runtimeTarget "whisper-server.exe") -Force }
    } else {
      Copy-Item -LiteralPath $customPath -Destination (Join-Path $runtimeTarget "whisper-cli.exe") -Force
    }
    Write-Host "whisper runtime pripraven v: $runtimeTarget" -ForegroundColor Green
    return
  }

  $zipUrl = "https://github.com/ggerganov/whisper.cpp/releases/download/v1.7.6/whisper-bin-x64.zip"
  $tmpRoot = Join-Path $env:TEMP "astt_whisper_runtime"
  Ensure-Dir $tmpRoot
  $zipPath = Join-Path $tmpRoot "whisper-bin-x64.zip"
  $extractDir = Join-Path $tmpRoot "extract"
  if (Test-Path -LiteralPath $extractDir) { Remove-Item -LiteralPath $extractDir -Recurse -Force }
  Invoke-WebRequest -UseBasicParsing -Uri $zipUrl -OutFile $zipPath
  Expand-Archive -LiteralPath $zipPath -DestinationPath $extractDir -Force

  $cli = Get-ChildItem -Path $extractDir -Recurse -Filter "whisper-cli.exe" | Select-Object -First 1
  $srv = Get-ChildItem -Path $extractDir -Recurse -Filter "whisper-server.exe" | Select-Object -First 1
  if (-not $cli) {
    throw "V runtime archivu nebyl nalezen whisper-cli.exe"
  }
  Copy-Item -LiteralPath $cli.FullName -Destination (Join-Path $runtimeTarget "whisper-cli.exe") -Force
  if ($srv) {
    Copy-Item -LiteralPath $srv.FullName -Destination (Join-Path $runtimeTarget "whisper-server.exe") -Force
  }
  Write-Host "whisper runtime pripraven v: $runtimeTarget" -ForegroundColor Green
}

function Install-VoskOptional([string]$Repo) {
  if (-not (Ask-YesNo "Volitelne: stahnout VOSK small cs-0.4?" $false)) { return }
  $modelStore = Join-Path $Repo "runtime\model_store"
  Ensure-Dir $modelStore
  $tmpRoot = Join-Path $env:TEMP "astt_vosk"
  Ensure-Dir $tmpRoot
  $zipPath = Join-Path $tmpRoot "vosk-small-cs-0.4.zip"
  $extractDir = Join-Path $tmpRoot "extract"
  if (Test-Path -LiteralPath $extractDir) { Remove-Item -LiteralPath $extractDir -Recurse -Force }
  $url = "https://alphacephei.com/vosk/models/vosk-model-small-cs-0.4-rhasspy.zip"
  Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zipPath
  Expand-Archive -LiteralPath $zipPath -DestinationPath $extractDir -Force
  $firstDir = Get-ChildItem -Path $extractDir | Where-Object { $_.PSIsContainer } | Select-Object -First 1
  if (-not $firstDir) { throw "Nepodarilo se najit rozbaleny VOSK model." }
  $target = Join-Path $modelStore "vosk_small_cs_0_4"
  if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
  Move-Item -LiteralPath $firstDir.FullName -Destination $target
  Write-Host "VOSK model nainstalovan do: $target" -ForegroundColor Green
}

function Install-HFOptional([string]$Repo) {
  if (-not (Ask-YesNo "Volitelne: stahnout HF modely (faster-whisper small/medium + Qwen3-ASR 0.6B)?" $false)) { return }
  $py = Pick-Python $Repo
  $venvPy = Join-Path $Repo ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPy) { $py = $venvPy }

  if ($py -eq "py -3.13") {
    & py -3.13 -m pip install huggingface_hub
  } else {
    & $py -m pip install huggingface_hub
  }

  $targets = @(
    @{ model_id = "faster_whisper_small_cs_int8"; repo_id = "Systran/faster-whisper-small"; size = "~1.1 GB" },
    @{ model_id = "faster_whisper_medium_cs_int8"; repo_id = "Systran/faster-whisper-medium"; size = "~2.5 GB" },
    @{ model_id = "qwen3_asr_0_6b"; repo_id = "Qwen/Qwen3-ASR-0.6B"; size = "~1.6 GB" }
  )
  foreach ($item in $targets) {
    if (-not (Ask-YesNo ("Stahnout {0} ({1})?" -f $item.model_id, $item.size) $false)) { continue }
    $targetDir = Join-Path $Repo ("runtime/model_store/{0}" -f $item.model_id)
    Ensure-Dir $targetDir
    $cmd = @"
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="$($item.repo_id)",
    local_dir=r"$targetDir",
    local_dir_use_symlinks=False,
    resume_download=True,
)
print("OK: $($item.model_id)")
"@
    $tmpPy = Join-Path $env:TEMP "astt_hf_download.py"
    Set-Content -LiteralPath $tmpPy -Value $cmd -Encoding UTF8
    if ($py -eq "py -3.13") {
      & py -3.13 $tmpPy
    } else {
      & $py $tmpPy
    }
  }
}

$repo = Resolve-RepoRoot $RepoRoot
Set-Location -LiteralPath $repo

Write-Head "aSTT-comp install onboarding (Windows)"
Write-Host "Repo root: $repo"

Write-Head "Prerequisites"
$missing = @()
foreach ($cmd in @("git", "npm", "ffmpeg")) {
  if (Test-Cmd $cmd) {
    Write-Host ("OK  - {0}" -f $cmd) -ForegroundColor Green
  } else {
    Write-Host ("MISS- {0}" -f $cmd) -ForegroundColor Yellow
    $missing += $cmd
  }
}
if ((Test-Cmd "py") -or (Test-Cmd "python")) {
  Write-Host "OK  - python/py" -ForegroundColor Green
} else {
  Write-Host "MISS- python/py" -ForegroundColor Yellow
  $missing += "python"
}
if ($missing.Count -gt 0) {
  Write-Host "Neco chybi: $($missing -join ', ')" -ForegroundColor Yellow
}

if ($DryRun) {
  exit (Invoke-DryRunValidation $repo $missing)
}

if (Ask-YesNo "Pripravit Python virtual env (.venv)?" $true) {
  if (-not (Test-Path -LiteralPath ".venv")) {
    if (Test-Cmd "py") {
      & py -3.13 -m venv .venv
    } else {
      & python -m venv .venv
    }
  }
  if (Ask-YesNo "Nainstalovat backend dependencies (backend/requirements.txt)?" $true) {
    & ".venv\Scripts\python.exe" -m pip install -r backend/requirements.txt
  }
}

if (Ask-YesNo "Nainstalovat frontend dependencies (npm --prefix frontend install)?" $true) {
  & npm --prefix frontend install
}
if (Ask-YesNo "Udelat frontend build (npm --prefix frontend run build)?" $true) {
  & npm --prefix frontend run build
}

Write-Head "whisper.cpp runtime"
if (Ask-YesNo "Pripravit whisper runtime (whisper-cli + whisper-server)?" $true) {
  Install-WhisperRuntime $repo
}

Write-Head "Core whisper.cpp modely"
$modelStore = Join-Path $repo "runtime\model_store"
Ensure-Dir $modelStore
$models = @(
  @{
    label = "whisper.cpp base"
    size = "~148 MB"
    url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"
    target = (Join-Path $modelStore "whisper_cpp_base/ggml-base.bin")
  },
  @{
    label = "whisper.cpp small"
    size = "~466 MB"
    url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin"
    target = (Join-Path $modelStore "whisper_cpp_small/ggml-small.bin")
  },
  @{
    label = "whisper.cpp large-v3 (q5_0)"
    size = "~1.55 GB"
    url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-q5_0.bin"
    target = (Join-Path $modelStore "whisper_cpp_large_v3/ggml-large-v3-q5_0.bin")
  },
  @{
    label = "whisper.cpp large-v3-turbo (q5_0)"
    size = "~547 MB"
    url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin"
    target = (Join-Path $modelStore "whisper_cpp_large_v3_turbo/ggml-large-v3-turbo-q5_0.bin")
  }
)
foreach ($m in $models) {
  [void](Download-FileWithPrompt $m.label $m.size $m.url $m.target)
}

Write-Head "Volitelne modely"
Install-VoskOptional $repo
Install-HFOptional $repo

Write-Host ""
Write-Host "Dalsi volitelny model:" -ForegroundColor White
Write-Host "- sherpa-onnx small: priprav lokalni bundle (tokens + encoder/decoder/joiner .onnx) do runtime/model_store/sherpa_onnx_small"
Write-Host ""
Write-Host "Kontrola instalace:"
Write-Host "  .venv\\Scripts\\python.exe scripts/check_model.py whisper_cpp_small"
Write-Host "  .venv\\Scripts\\python.exe scripts/check_health.py"
Write-Host "  otevri: http://127.0.0.1:8012/models"
Write-Host ""
Write-Host "Naposledy: docs/install_help.txt a /api/docs/install-help.txt"
