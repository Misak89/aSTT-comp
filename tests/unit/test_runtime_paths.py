from __future__ import annotations

from pathlib import Path
import shutil
import uuid

from packages.common import runtime_paths


def _workspace_tmp_root() -> Path:
    root = runtime_paths.project_root() / "runtime" / "_test_runtime_paths"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _fresh_dir(name: str) -> Path:
    path = _workspace_tmp_root() / f"{name}_{uuid.uuid4().hex[:8]}"
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_runtime_root_defaults_to_repo_runtime(monkeypatch):
    monkeypatch.delenv("ASTT_RUNTIME_ROOT", raising=False)
    expected = runtime_paths.project_root() / "runtime"
    assert runtime_paths.runtime_root() == expected


def test_runtime_root_respects_env_override(monkeypatch):
    override = _fresh_dir("override_root")
    monkeypatch.setenv("ASTT_RUNTIME_ROOT", str(override))
    assert runtime_paths.runtime_root() == override.resolve()


def test_runtime_candidates_prioritize_canonical(monkeypatch):
    canonical = _fresh_dir("canonical_candidates")
    legacy = _fresh_dir("legacy_candidates")
    monkeypatch.setenv("ASTT_RUNTIME_ROOT", str(canonical))
    monkeypatch.setattr(runtime_paths, "legacy_runtime_root", lambda: legacy)

    candidates = runtime_paths.runtime_candidates("model_store", "whisper_cpp_small")
    assert candidates[0] == canonical / "model_store" / "whisper_cpp_small"
    assert candidates[1] == legacy / "model_store" / "whisper_cpp_small"


def test_first_existing_runtime_path_prefers_existing_candidate(monkeypatch):
    canonical = _fresh_dir("canonical_existing")
    legacy = _fresh_dir("legacy_existing")
    monkeypatch.setenv("ASTT_RUNTIME_ROOT", str(canonical))
    monkeypatch.setattr(runtime_paths, "legacy_runtime_root", lambda: legacy)

    legacy_model = legacy / "model_store" / "test_model"
    legacy_model.mkdir(parents=True, exist_ok=True)

    resolved = runtime_paths.first_existing_runtime_path("model_store", "test_model")
    assert resolved == legacy_model

    canonical_model = canonical / "model_store" / "test_model"
    canonical_model.mkdir(parents=True, exist_ok=True)
    resolved_after = runtime_paths.first_existing_runtime_path("model_store", "test_model")
    assert resolved_after == canonical_model
