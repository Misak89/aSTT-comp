"""
Central configuration — all runtime paths and constants in one place.
"""
from packages.common.runtime_paths import project_root, runtime_root

# Repository root
ROOT = project_root()

# Runtime storage (gitignored)
RUNTIME_ROOT = runtime_root()
LIBRARY_ROOT = RUNTIME_ROOT / "library"
SUBTITLES_ROOT = LIBRARY_ROOT / "subtitles"
RESULTS_ROOT = LIBRARY_ROOT / "results"
RUNS_ROOT = RUNTIME_ROOT / "runs"
JOBS_ROOT = RUNTIME_ROOT / "jobs"
SCENARIOS_ROOT = RUNTIME_ROOT / "scenarios"
TUNING_ROOT = RUNTIME_ROOT / "tuning"
MODEL_STORE_ROOT = RUNTIME_ROOT / "model_store"
AUDIO_CACHE_ROOT = RUNTIME_ROOT / "audio_cache"
MIC_SESSIONS_ROOT = RUNTIME_ROOT / "mic_sessions"
MIC_SEQUENCES_ROOT = RUNTIME_ROOT / "mic_sequences"
LATE_MIC_ROOT = RUNTIME_ROOT / "late_mic"
TRANSCRIPTS_ROOT = RUNTIME_ROOT / "transcripts"
LOGGER_LOGS_ROOT = RUNTIME_ROOT / "logs"

# Model install/uninstall event logs (docs/models/{model_id}.json)
MODELS_LOG_ROOT = ROOT / "docs" / "models"

# Server
HOST = "127.0.0.1"
PORT = 8012

# Ensure dirs exist at import time
for _d in (LIBRARY_ROOT, SUBTITLES_ROOT, RESULTS_ROOT, RUNS_ROOT, JOBS_ROOT,
           SCENARIOS_ROOT, MODEL_STORE_ROOT, MODELS_LOG_ROOT, TUNING_ROOT, AUDIO_CACHE_ROOT,
           MIC_SESSIONS_ROOT, MIC_SEQUENCES_ROOT, LATE_MIC_ROOT, TRANSCRIPTS_ROOT, LOGGER_LOGS_ROOT):
    _d.mkdir(parents=True, exist_ok=True)
