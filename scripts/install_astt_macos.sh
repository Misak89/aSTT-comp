#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=""
DRY_RUN=0
STRICT_PREREQ=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      ;;
    --strict-prereq)
      STRICT_PREREQ=1
      ;;
    --repo-root)
      shift
      REPO_ROOT="${1:-}"
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
    *)
      if [[ -z "${REPO_ROOT}" ]]; then
        REPO_ROOT="$1"
      else
        echo "Unexpected positional argument: $1" >&2
        exit 2
      fi
      ;;
  esac
  shift
done

if [[ -z "${REPO_ROOT}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi
cd "${REPO_ROOT}"

say_head() {
  echo
  echo "== $1 =="
}

ask_yn() {
  local prompt="$1"
  local def="${2:-Y}"
  local suffix="[Y/n]"
  if [[ "${def}" != "Y" ]]; then
    suffix="[y/N]"
  fi
  while true; do
    read -r -p "${prompt} ${suffix} " ans
    ans="${ans:-$def}"
    case "${ans}" in
      y|Y|yes|YES|a|A|ano|ANO) return 0 ;;
      n|N|no|NO|ne|NE) return 1 ;;
      *) echo "Please answer y or n." ;;
    esac
  done
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

require_file() {
  if [[ ! -e "$1" ]]; then
    echo "Missing required file/dir: $1" >&2
    return 1
  fi
}

pick_python() {
  if [[ -x ".venv/bin/python" ]]; then
    echo ".venv/bin/python"
    return 0
  fi
  if has_cmd python3.13; then
    echo "python3.13"
    return 0
  fi
  if has_cmd python3; then
    echo "python3"
    return 0
  fi
  echo "python3"
}

test_repo_writable() {
  local runtime_dir="${REPO_ROOT}/runtime"
  local probe_parent="${REPO_ROOT}"
  if [[ -d "${runtime_dir}" ]]; then
    probe_parent="${runtime_dir}"
  fi
  local probe="${probe_parent}/.astt_install_dry_run_$$_${RANDOM}.tmp"
  printf "ok\n" > "${probe}"
  rm -f "${probe}"
}

run_dry_run_validation() {
  say_head "Dry-run install validation"
  echo "Dry-run: no packages are installed and no models are downloaded."

  require_file "${REPO_ROOT}/backend/requirements.txt"
  require_file "${REPO_ROOT}/frontend/package.json"
  require_file "${REPO_ROOT}/frontend/package-lock.json"
  require_file "${REPO_ROOT}/scripts/webctl.py"
  require_file "${REPO_ROOT}/scripts/check_health.py"
  require_file "${REPO_ROOT}/scripts/portability_audit.py"
  test_repo_writable

  if [[ -x ".venv/bin/python" ]] || has_cmd python3.13 || has_cmd python3; then
    echo "OK   - python candidate: $(pick_python)"
  else
    echo "MISS - python candidate"
    missing+=("python3")
  fi

  echo
  echo "Planned full-install steps:"
  echo "  1. create/update .venv"
  echo "  2. install backend/requirements.txt"
  echo "  3. npm --prefix frontend install"
  echo "  4. npm --prefix frontend run build"
  echo "  5. prepare whisper.cpp runtime"
  echo "  6. download selected models into runtime/model_store"
  echo "  7. verify scripts/check_health.py and scripts/check_model.py"

  if [[ "${STRICT_PREREQ}" == "1" && ${#missing[@]} -gt 0 ]]; then
    echo "Dry-run FAIL: missing prerequisites: ${missing[*]}" >&2
    return 1
  fi
  echo "Dry-run OK"
}

download_with_prompt() {
  local label="$1"
  local size="$2"
  local url="$3"
  local target="$4"
  echo
  echo "${label} (${size})"
  echo "URL: ${url}"
  if ! ask_yn "Download this package?" "Y"; then
    echo "Skipped: ${label}"
    return 0
  fi
  mkdir -p "$(dirname "${target}")"
  curl -L --fail "${url}" -o "${target}"
  local mb
  local py
  py="$(pick_python)"
  mb=$("${py}" - <<PY
from pathlib import Path
p = Path(r"${target}")
print(round(p.stat().st_size / (1024*1024), 1))
PY
)
  echo "Done -> ${target} (${mb} MB)"
}

install_whisper_runtime() {
  local target_dir="${REPO_ROOT}/runtime/model_store/whisper_cpp"
  mkdir -p "${target_dir}"
  read -r -p "Path to whisper-cli (or dir with whisper-cli/whisper-server, Enter = auto-detect): " custom
  if [[ -n "${custom}" ]]; then
    custom="$(cd "$(dirname "${custom}")" && pwd)/$(basename "${custom}")"
    if [[ -d "${custom}" ]]; then
      if [[ -x "${custom}/whisper-cli" ]]; then
        cp -f "${custom}/whisper-cli" "${target_dir}/whisper-cli"
      fi
      if [[ -x "${custom}/whisper-server" ]]; then
        cp -f "${custom}/whisper-server" "${target_dir}/whisper-server"
      fi
    else
      cp -f "${custom}" "${target_dir}/whisper-cli"
      chmod +x "${target_dir}/whisper-cli"
    fi
    echo "whisper runtime ready in ${target_dir}"
    return 0
  fi

  if has_cmd whisper-cli; then
    cp -f "$(command -v whisper-cli)" "${target_dir}/whisper-cli"
    chmod +x "${target_dir}/whisper-cli"
    if has_cmd whisper-server; then
      cp -f "$(command -v whisper-server)" "${target_dir}/whisper-server"
      chmod +x "${target_dir}/whisper-server"
    fi
    echo "whisper runtime copied from PATH to ${target_dir}"
    return 0
  fi

  echo "whisper-cli not found in PATH."
  echo "Install it first (recommended): brew install whisper-cpp"
}

install_vosk_optional() {
  if ! ask_yn "Optional: download VOSK small cs-0.4?" "N"; then
    return 0
  fi
  if ! has_cmd unzip; then
    echo "unzip not found. Install unzip and rerun this optional step."
    return 0
  fi
  local model_store="${REPO_ROOT}/runtime/model_store"
  local tmp_dir
  tmp_dir="$(mktemp -d)"
  local zip_path="${tmp_dir}/vosk-small-cs-0.4.zip"
  local ex_dir="${tmp_dir}/extract"
  mkdir -p "${ex_dir}"
  curl -L --fail "https://alphacephei.com/vosk/models/vosk-model-small-cs-0.4-rhasspy.zip" -o "${zip_path}"
  unzip -q "${zip_path}" -d "${ex_dir}"
  local first_dir
  first_dir="$(find "${ex_dir}" -mindepth 1 -maxdepth 1 -type d | head -n 1 || true)"
  if [[ -z "${first_dir}" ]]; then
    echo "Failed to detect unpacked VOSK directory."
    rm -rf "${tmp_dir}"
    return 1
  fi
  rm -rf "${model_store}/vosk_small_cs_0_4"
  mkdir -p "${model_store}"
  mv "${first_dir}" "${model_store}/vosk_small_cs_0_4"
  rm -rf "${tmp_dir}"
  echo "VOSK installed -> ${model_store}/vosk_small_cs_0_4"
}

install_hf_optional() {
  if ! ask_yn "Optional: download HF models (faster-whisper small/medium + Qwen3-ASR 0.6B)?" "N"; then
    return 0
  fi
  local py
  py="$(pick_python)"
  "${py}" -m pip install huggingface_hub

  while IFS="|" read -r model_id repo_id approx; do
    [[ -z "${model_id}" ]] && continue
    if ! ask_yn "Download ${model_id} (${approx})?" "N"; then
      continue
    fi
    local target_dir="${REPO_ROOT}/runtime/model_store/${model_id}"
    mkdir -p "${target_dir}"
    "${py}" - <<PY
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="${repo_id}",
    local_dir=r"${target_dir}",
    local_dir_use_symlinks=False,
    resume_download=True,
)
print("OK: ${model_id}")
PY
  done <<'EOF'
faster_whisper_small_cs_int8|Systran/faster-whisper-small|~1.1 GB
faster_whisper_medium_cs_int8|Systran/faster-whisper-medium|~2.5 GB
qwen3_asr_0_6b|Qwen/Qwen3-ASR-0.6B|~1.6 GB
EOF
}

say_head "aSTT-comp install onboarding (macOS)"
echo "Repo root: ${REPO_ROOT}"

say_head "Prerequisites"
missing=()
for cmd in git npm ffmpeg; do
  if has_cmd "${cmd}"; then
    echo "OK   - ${cmd}"
  else
    echo "MISS - ${cmd}"
    missing+=("${cmd}")
  fi
done
if has_cmd python3.13 || has_cmd python3; then
  echo "OK   - python3"
else
  echo "MISS - python3"
  missing+=("python3")
fi
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "Missing prerequisites: ${missing[*]}"
fi

if [[ "${DRY_RUN}" == "1" ]]; then
  run_dry_run_validation
  exit $?
fi

if ask_yn "Create/update Python virtual env (.venv)?" "Y"; then
  if [[ ! -d ".venv" ]]; then
    if has_cmd python3.13; then
      python3.13 -m venv .venv
    else
      python3 -m venv .venv
    fi
  fi
  if ask_yn "Install backend dependencies (backend/requirements.txt)?" "Y"; then
    .venv/bin/python -m pip install -r backend/requirements.txt
  fi
fi

if ask_yn "Install frontend dependencies (npm --prefix frontend install)?" "Y"; then
  npm --prefix frontend install
fi
if ask_yn "Build frontend (npm --prefix frontend run build)?" "Y"; then
  npm --prefix frontend run build
fi

say_head "whisper.cpp runtime"
if ask_yn "Prepare whisper runtime (whisper-cli + whisper-server)?" "Y"; then
  install_whisper_runtime
fi

say_head "Core whisper.cpp models"
MODEL_STORE="${REPO_ROOT}/runtime/model_store"
mkdir -p "${MODEL_STORE}"

download_with_prompt \
  "whisper.cpp base" "~148 MB" \
  "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin" \
  "${MODEL_STORE}/whisper_cpp_base/ggml-base.bin"

download_with_prompt \
  "whisper.cpp small" "~466 MB" \
  "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin" \
  "${MODEL_STORE}/whisper_cpp_small/ggml-small.bin"

download_with_prompt \
  "whisper.cpp large-v3 (q5_0)" "~1.55 GB" \
  "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-q5_0.bin" \
  "${MODEL_STORE}/whisper_cpp_large_v3/ggml-large-v3-q5_0.bin"

download_with_prompt \
  "whisper.cpp large-v3-turbo (q5_0)" "~547 MB" \
  "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin" \
  "${MODEL_STORE}/whisper_cpp_large_v3_turbo/ggml-large-v3-turbo-q5_0.bin"

say_head "Optional models"
install_vosk_optional
install_hf_optional
echo "Optional sherpa-onnx small: put bundle files (tokens + encoder/decoder/joiner .onnx) into runtime/model_store/sherpa_onnx_small"

echo
echo "Verification:"
echo "  .venv/bin/python scripts/check_model.py whisper_cpp_small"
echo "  .venv/bin/python scripts/check_health.py"
echo "  open: http://127.0.0.1:8012/models"
echo
echo "See: docs/install_help.txt and /api/docs/install-help.txt"
