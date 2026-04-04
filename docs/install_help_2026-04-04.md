# aSTT-comp Install Onboarding (Windows + macOS)

Doc-Meta:
- owner: engineering
- status: active
- doc_file: install_help_2026-04-04.md
- last_updated_utc: 2026-04-04T08:05:00Z
- review_due_utc: 2026-04-15T00:00:00Z

Global Authority (Repository-wide): AGENTS.md is the only top-level Source of Truth for all project rules and interpretation. If any document conflicts with AGENTS.md, AGENTS.md always prevails.
Mandatory Start (in this repository): README.md -> AGENTS.md -> docs/PLAN_TRACKER.md.

## Scope
- Kompletní onboarding pro celou app `aSTT-comp` (nejen modely):
  - prerequisite software
  - install app
  - start app
  - install STT modely
  - verifikace komunikace app + model runtime

## Prerequisites
### Windows
- Git
- Python 3.13+
- Node.js 18+
- ffmpeg v `PATH`

### macOS
- Git
- Python 3.13+ (nebo kompatibilní 3.x)
- Node.js 18+
- ffmpeg
- doporučeno Homebrew

## Install app
### Windows
```bat
git clone https://github.com/Misak89/aSTT-comp.git
cd aSTT-comp
py -3.13 -m venv .venv
.venv\Scripts\pip install -r backend/requirements.txt
npm --prefix frontend install
npm --prefix frontend run build
web-up.cmd
```

### macOS
```bash
git clone https://github.com/Misak89/aSTT-comp.git
cd aSTT-comp
python3.13 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
npm --prefix frontend install
npm --prefix frontend run build
./web-up.sh
```

## Interactive scripts
- Windows: `scripts/install_astt_windows.ps1`
- macOS: `scripts/install_astt_macos.sh`

Script flow:
- check prerequisites
- install venv/backend/frontend (prompt-driven)
- setup whisper runtime (`whisper-cli`, `whisper-server`)
- before each core whisper model download show size and ask download/skip
- optional flows for VOSK + HuggingFace models

## Core model minimum
- whisper.cpp base
- whisper.cpp small
- whisper.cpp large-v3
- whisper.cpp large-v3-turbo

Expected files:
- `runtime/model_store/whisper_cpp_base/ggml-base.bin`
- `runtime/model_store/whisper_cpp_small/ggml-small.bin`
- `runtime/model_store/whisper_cpp_large_v3/ggml-large-v3-q5_0.bin` (or compatible large-v3 file)
- `runtime/model_store/whisper_cpp_large_v3_turbo/ggml-large-v3-turbo-q5_0.bin`

## Optional models
- sherpa-onnx small (bundle to `runtime/model_store/sherpa_onnx_small`)
- VOSK small cs-0.4 (`runtime/model_store/vosk_small_cs_0_4`)
- faster-whisper small/medium (CTranslate2 folders)
- Qwen3-ASR 0.6B

## Current model overview (CZ support + approx size)
- whisper.cpp base: CZ yes, ~148 MB
- whisper.cpp small: CZ yes, ~466 MB
- whisper.cpp large-v3 q5_0: CZ yes, ~1.55 GB
- whisper.cpp large-v3-turbo q5_0: CZ yes, ~547 MB
- faster-whisper small (CZ int8 profile): CZ yes, ~1.1 GB
- faster-whisper medium (CZ int8 profile): CZ yes, ~2.5 GB
- VOSK small cs-0.4: CZ yes, ~40-60 MB
- sherpa-onnx small: CZ bundle-dependent, ~50-200 MB
- sherpa-onnx Parakeet 0.6B int8 (CZ): CZ yes, ~700 MB to ~1.5 GB
- Qwen3-ASR 0.6B: CZ yes, ~1.6 GB
- Qwen3-ASR 1.7B: CZ yes, ~4-5 GB
- Moonshine Medium (EN): CZ no

## Verification
```bash
python scripts/check_health.py
python scripts/check_model.py whisper_cpp_small
```

UI check:
- `http://127.0.0.1:8012/models`

## Links and exact locations
- Human help file: `docs/install_help.txt`
- API link: `http://127.0.0.1:8012/api/docs/install-help.txt`
- Models page: `http://127.0.0.1:8012/models`
- Scripts:
  - `scripts/install_astt_windows.ps1`
  - `scripts/install_astt_macos.sh`
