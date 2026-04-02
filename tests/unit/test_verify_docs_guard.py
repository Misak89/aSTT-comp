from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "verify_docs_guard.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_docs_guard", MODULE_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_run_uses_utf8_decoding(monkeypatch):
    mod = _load_module()
    called: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        called["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    _ = mod._run(["git", "status"])

    kwargs = called["kwargs"]
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert kwargs["text"] is True


def test_run_text_uses_utf8_decoding(monkeypatch):
    mod = _load_module()
    called: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        called["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    out = mod._run_text(["git", "status"])

    kwargs = called["kwargs"]
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert kwargs["text"] is True
    assert out == "ok"


def test_show_file_uses_utf8_decoding(monkeypatch):
    mod = _load_module()
    called: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        called["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="český řetězec 😀", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    out = mod._show_file("HEAD", "docs/session_log.md")

    kwargs = called["kwargs"]
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert kwargs["text"] is True
    assert "český" in out
