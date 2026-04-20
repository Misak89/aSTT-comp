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


def test_validate_new_jsonl_doc_file_contract_passes():
    mod = _load_module()
    content = (
        '{"doc_meta":{"doc_file":"sample_2026-04-02.jsonl"},"event":"doc_created"}\n'
        '{"event":"snapshot_published"}\n'
    )
    failures = mod._validate_new_doc_file_contract(
        path="docs/reports/sample_2026-04-02.jsonl",
        content=content,
    )
    assert failures == []


def test_validate_new_jsonl_doc_file_contract_fails_without_doc_meta():
    mod = _load_module()
    content = '{"event":"doc_created"}\n{"event":"snapshot_published"}\n'
    failures = mod._validate_new_doc_file_contract(
        path="docs/reports/sample_2026-04-02.jsonl",
        content=content,
    )
    assert failures
    assert "doc_meta" in failures[0]


def test_txt_file_under_docs_is_not_guarded_triplet_format():
    mod = _load_module()
    assert not mod._is_guarded_doc_format_path("docs/log_cmd_help.txt")


def test_triplet_exempt_for_models_and_runs_paths():
    mod = _load_module()
    assert mod._is_triplet_exempt("docs/models/whisper_cpp_small.json")
    assert mod._is_triplet_exempt("docs/runs/run_1/README.md")
    assert not mod._is_triplet_exempt("docs/reports/audit_conclusion_2026-04-02.json")


def test_needs_v7_plan_tracker_sync_for_v7_service_change():
    mod = _load_module()
    changed = ["backend/app/services/mic_service.py"]
    assert mod._needs_v7_plan_tracker_sync(changed) is True


def test_detects_added_v7_completion_claim_from_patch(monkeypatch):
    mod = _load_module()
    patch = (
        "@@ -1,0 +1,2 @@\n"
        "+## Session 2026-04-20T18:50:31Z\n"
        "+- Implementovany body 1-7 pro V7 online mic orchestrator.\n"
    )
    monkeypatch.setattr(mod, "_get_patch", lambda base, head, path: patch)
    assert mod._has_added_v7_completion_claim("base", "head", "docs/session_log.md") is True


def test_main_fails_when_v7_code_changes_without_plan_tracker_update(monkeypatch, capsys):
    mod = _load_module()
    changed = [
        "backend/app/services/mic_service.py",
        "docs/session_log.md",
    ]

    monkeypatch.setattr(mod.argparse.ArgumentParser, "parse_args", lambda self: SimpleNamespace(base=None, head="HEAD"))
    monkeypatch.setattr(mod, "_get_changed", lambda base, head: changed)
    monkeypatch.setattr(mod, "_get_name_status", lambda base, head: {})
    monkeypatch.setattr(mod, "_get_numstat", lambda base, head: [("backend/app/services/mic_service.py", 10, 1)])
    monkeypatch.setattr(mod, "_get_head_doc_exts_by_stem", lambda head: {})
    monkeypatch.setattr(mod, "_show_file", lambda ref, path: "Doc-Meta:\n- owner: engineering\n")
    monkeypatch.setattr(mod, "_validate_core_doc", lambda path, content: [])
    monkeypatch.setattr(mod, "_has_substantive_added_lines", lambda base, head, path: True)
    monkeypatch.setattr(mod, "_has_new_session_header", lambda base, head, path: True)
    monkeypatch.setattr(mod, "_has_added_prefix_line", lambda base, head, path, prefix: True)
    monkeypatch.setattr(mod, "_needs_arch_update", lambda changed_paths: False)
    monkeypatch.setattr(mod, "_needs_runbook_update", lambda changed_paths: False)
    monkeypatch.setattr(mod, "_needs_plan_tracker_update", lambda changed_paths: False)
    monkeypatch.setattr(mod, "_needs_oss_intake_update", lambda changed_paths: False)
    monkeypatch.setattr(mod, "_has_added_v7_completion_claim", lambda base, head, path: False)

    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 1
    assert "V7 code/test or V7 completion claim changed" in out


def test_main_passes_when_v7_code_changes_with_plan_tracker_update(monkeypatch, capsys):
    mod = _load_module()
    changed = [
        "backend/app/services/mic_service.py",
        "docs/session_log.md",
        "docs/PLAN_TRACKER.md",
    ]

    monkeypatch.setattr(mod.argparse.ArgumentParser, "parse_args", lambda self: SimpleNamespace(base=None, head="HEAD"))
    monkeypatch.setattr(mod, "_get_changed", lambda base, head: changed)
    monkeypatch.setattr(mod, "_get_name_status", lambda base, head: {})
    monkeypatch.setattr(mod, "_get_numstat", lambda base, head: [("backend/app/services/mic_service.py", 12, 2)])
    monkeypatch.setattr(mod, "_get_head_doc_exts_by_stem", lambda head: {})
    monkeypatch.setattr(mod, "_show_file", lambda ref, path: "Doc-Meta:\n- owner: engineering\n")
    monkeypatch.setattr(mod, "_validate_core_doc", lambda path, content: [])
    monkeypatch.setattr(mod, "_has_substantive_added_lines", lambda base, head, path: True)
    monkeypatch.setattr(mod, "_has_new_session_header", lambda base, head, path: True)
    monkeypatch.setattr(mod, "_has_added_prefix_line", lambda base, head, path, prefix: True)
    monkeypatch.setattr(mod, "_needs_arch_update", lambda changed_paths: False)
    monkeypatch.setattr(mod, "_needs_runbook_update", lambda changed_paths: False)
    monkeypatch.setattr(mod, "_needs_plan_tracker_update", lambda changed_paths: False)
    monkeypatch.setattr(mod, "_needs_oss_intake_update", lambda changed_paths: False)
    monkeypatch.setattr(mod, "_has_added_v7_completion_claim", lambda base, head, path: False)

    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "docs-guard: OK" in out
