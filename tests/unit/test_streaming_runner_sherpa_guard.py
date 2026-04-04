from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import shutil
import uuid

import pytest

from packages.benchmarks.runners import streaming_runner
from packages.benchmarks.runners.streaming_runner import StreamingRunConfig


def _bundle(model_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        tokens=str(model_dir / "tokens.txt"),
        encoder=str(model_dir / "encoder.onnx"),
        decoder=str(model_dir / "decoder.onnx"),
        joiner=str(model_dir / "joiner.onnx"),
        model_dir=str(model_dir),
    )


def _patch_sherpa_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    import packages.adapters.sherpa_onnx_runner as sherpa

    monkeypatch.setattr(sherpa, "create_sherpa_live_session", lambda config: {"ok": True})
    monkeypatch.setattr(sherpa, "transcribe_sherpa_live_chunk", lambda **kwargs: {"text": ""})
    monkeypatch.setattr(sherpa, "finalize_sherpa_live_session", lambda **kwargs: {"text": ""})


def _fresh_root() -> Path:
    root = Path(__file__).resolve().parents[2] / "runtime" / "_test_streaming_runner_sherpa_guard"
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"case_{uuid.uuid4().hex[:8]}"
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target


def test_sherpa_small_prefers_scoped_root(monkeypatch: pytest.MonkeyPatch):
    import packages.adapters.sherpa_onnx_runner as sherpa

    _patch_sherpa_functions(monkeypatch)
    root = _fresh_root()

    model_store = root / "model_store"
    scoped_root = model_store / "sherpa_onnx_small"
    scoped_root.mkdir(parents=True, exist_ok=True)
    (model_store / "sherpa_onnx_parakeet_cs_int8").mkdir(parents=True, exist_ok=True)

    calls: list[Path] = []

    def fake_resolve(root: str | Path, preferred_language: str | None = None):
        path = Path(root)
        calls.append(path)
        if path == scoped_root:
            return _bundle(scoped_root / "sherpa-onnx-streaming-en-20M-2023-02-17")
        return _bundle(model_store / "sherpa_onnx_parakeet_cs_int8" / "parakeet-tdt-0.6b-v3")

    monkeypatch.setattr(sherpa, "resolve_sherpa_model_bundle", fake_resolve)

    cfg = StreamingRunConfig(
        model_id="sherpa_onnx_small",
        model_params={},
        model_store_root=str(model_store),
        output_dir=str(root / "out"),
    )
    _, _, _, run_cfg = streaming_runner._build_live_session_components(adapter="sherpa_onnx", config=cfg)

    assert calls == [scoped_root]
    assert "sherpa_onnx_small" in str(run_cfg.tokens)


def test_sherpa_small_falls_back_to_root_when_scoped_missing_bundle(
    monkeypatch: pytest.MonkeyPatch,
):
    import packages.adapters.sherpa_onnx_runner as sherpa

    _patch_sherpa_functions(monkeypatch)
    root = _fresh_root()

    model_store = root / "model_store"
    scoped_root = model_store / "sherpa_onnx_small"
    scoped_root.mkdir(parents=True, exist_ok=True)
    model_store.mkdir(parents=True, exist_ok=True)

    calls: list[Path] = []

    def fake_resolve(root: str | Path, preferred_language: str | None = None):
        path = Path(root)
        calls.append(path)
        if path == scoped_root:
            return None
        if path == model_store:
            return _bundle(model_store / "sherpa_onnx_streaming_en_zipformer")
        return None

    monkeypatch.setattr(sherpa, "resolve_sherpa_model_bundle", fake_resolve)

    cfg = StreamingRunConfig(
        model_id="sherpa_onnx_small",
        model_params={},
        model_store_root=str(model_store),
        output_dir=str(root / "out"),
    )
    _, _, _, run_cfg = streaming_runner._build_live_session_components(adapter="sherpa_onnx", config=cfg)

    assert calls == [scoped_root, model_store]
    assert "sherpa_onnx_streaming_en_zipformer" in str(run_cfg.tokens)


def test_sherpa_small_rejects_parakeet_bundle_mapping(monkeypatch: pytest.MonkeyPatch):
    import packages.adapters.sherpa_onnx_runner as sherpa

    _patch_sherpa_functions(monkeypatch)
    root = _fresh_root()

    model_store = root / "model_store"
    scoped_root = model_store / "sherpa_onnx_small"
    scoped_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        sherpa,
        "resolve_sherpa_model_bundle",
        lambda root, preferred_language=None: _bundle(
            model_store / "sherpa_onnx_parakeet_cs_int8" / "parakeet-tdt-0.6b-v3"
        ),
    )

    cfg = StreamingRunConfig(
        model_id="sherpa_onnx_small",
        model_params={},
        model_store_root=str(model_store),
        output_dir=str(root / "out"),
    )

    with pytest.raises(RuntimeError, match="nekompatibilní Parakeet bundle"):
        streaming_runner._build_live_session_components(adapter="sherpa_onnx", config=cfg)
