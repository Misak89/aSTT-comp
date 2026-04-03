from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HEALTH_ROUTER = ROOT / "backend" / "app" / "routers" / "health.py"


def _src() -> str:
    return HEALTH_ROUTER.read_text(encoding="utf-8")


def test_health_processes_default_mode_is_fast():
    src = _src()
    assert 'def app_processes_health(mode: str = Query(default="fast"))' in src


def test_health_processes_invalid_mode_falls_back_to_fast():
    src = _src()
    assert 'mode_norm = str(mode or "fast").strip().lower()' in src
    assert 'if mode_norm not in {"fast", "slow", "full"}:' in src
    assert 'mode_norm = "fast"' in src


def test_process_scan_uses_pid_metadata_ttl_cache():
    src = _src()
    assert "_PROC_META_CACHE" in src
    assert "def _get_cached_process_meta(" in src
    assert "def _put_cached_process_meta(" in src
    assert "_cleanup_proc_meta_cache(current_observed_pids, now_ts)" in src
