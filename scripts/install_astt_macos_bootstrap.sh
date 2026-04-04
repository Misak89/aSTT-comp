#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/Misak89/aSTT-comp.git"
DEFAULT_BRANCH="${ASTT_BRANCH:-feature/tuning-v6-docs}"
DEFAULT_TARGET_DIR="${HOME}/aSTT-comp"

say_head() {
  echo
  echo "== $1 =="
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
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

say_head "aSTT-comp macOS bootstrap installer"

missing=()
for cmd in git bash; do
  if ! has_cmd "${cmd}"; then
    missing+=("${cmd}")
  fi
done
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "Missing required commands: ${missing[*]}"
  echo "Install these tools first and rerun installer."
  exit 1
fi

read -r -p "Install directory (Enter = ${DEFAULT_TARGET_DIR}): " target_dir
target_dir="${target_dir:-$DEFAULT_TARGET_DIR}"

read -r -p "Git branch to install (Enter = ${DEFAULT_BRANCH}): " branch_name
branch_name="${branch_name:-$DEFAULT_BRANCH}"

if [[ -d "${target_dir}/.git" ]]; then
  say_head "Updating existing repository"
  git -C "${target_dir}" fetch origin
  git -C "${target_dir}" checkout "${branch_name}"
  git -C "${target_dir}" pull --ff-only origin "${branch_name}"
else
  if [[ -e "${target_dir}" ]] && [[ ! -d "${target_dir}" ]]; then
    echo "Target path exists but is not a directory: ${target_dir}"
    exit 1
  fi
  if [[ -d "${target_dir}" ]] && [[ -n "$(ls -A "${target_dir}" 2>/dev/null || true)" ]]; then
    echo "Target directory is not empty: ${target_dir}"
    if ! ask_yn "Continue and use this folder anyway?" "N"; then
      exit 1
    fi
  fi
  say_head "Cloning repository"
  git clone --branch "${branch_name}" "${REPO_URL}" "${target_dir}"
fi

say_head "Running full onboarding installer"
bash "${target_dir}/scripts/install_astt_macos.sh" "${target_dir}"

if ask_yn "Start app now (web-up.sh)?" "Y"; then
  bash "${target_dir}/web-up.sh"
fi

echo
echo "Done. Guide:"
echo "  ${target_dir}/docs/install_help_mac.txt"
echo "  ${target_dir}/docs/install_help.txt"
