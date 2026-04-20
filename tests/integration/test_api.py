"""
Integration testy — volají živý backend na 127.0.0.1:8012.
Přeskočí se automaticky pokud backend neběží (skip, ne fail).
"""
import sys
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

BASE_URL = "http://127.0.0.1:8012"


def _get(path: str) -> tuple[int, dict]:
    """Jednoduchý HTTP GET bez závislosti na requests."""
    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.URLError:
        return 0, {}


def _post(path: str, body: dict) -> tuple[int, dict]:
    import urllib.request
    import urllib.error
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())
    except urllib.error.URLError:
        return 0, {}


@pytest.fixture(scope="session", autouse=True)
def require_backend():
    status, _ = _get("/api/health")
    if status == 0:
        pytest.skip("Backend neběží (127.0.0.1:8012) — spusť uvicorn a znovu spusť testy")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health_returns_200():
    status, body = _get("/api/health")
    assert status == 200


def test_health_has_status_ok():
    _, body = _get("/api/health")
    assert body.get("status") == "ok"


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------

def test_library_items_returns_list():
    status, body = _get("/api/library/items")
    assert status == 200
    assert isinstance(body, list)


# ---------------------------------------------------------------------------
# Benchmark options
# ---------------------------------------------------------------------------

def test_benchmark_options_has_models():
    status, body = _get("/api/benchmark/options")
    assert status == 200
    assert "models" in body


def test_benchmark_options_has_settings():
    _, body = _get("/api/benchmark/options")
    assert "settings" in body


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def test_jobs_list_returns_jobs_envelope():
    status, body = _get("/api/benchmark/jobs")
    assert status == 200
    assert isinstance(body, dict)
    assert "jobs" in body
    assert isinstance(body["jobs"], list)


def test_create_job_missing_sources_returns_422():
    status, _ = _post("/api/benchmark/jobs", {})
    assert status == 422


def test_create_job_valid_returns_202():
    status, body = _post("/api/benchmark/jobs", {
        "video_ids": ["R3BsjbDtWrY"],
        "model_ids": ["whisper_cpp_small"],
        "evaluation_mode": "streaming",
        "sample_seconds": 30,
        "chunk_seconds": 30,
    })
    assert status == 202
    assert "job_id" in body


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def test_runs_returns_list():
    status, body = _get("/api/runs")
    assert status == 200
    assert isinstance(body, list)
