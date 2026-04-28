from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_windows_installer_exposes_noninteractive_dry_run():
    src = (ROOT / "scripts" / "install_astt_windows.ps1").read_text(encoding="utf-8")
    assert "[switch]$DryRun" in src
    assert "[switch]$StrictPrereq" in src
    assert "Invoke-DryRunValidation" in src


def test_macos_installer_exposes_noninteractive_dry_run():
    src = (ROOT / "scripts" / "install_astt_macos.sh").read_text(encoding="utf-8")
    assert "--dry-run" in src
    assert "--strict-prereq" in src
    assert "run_dry_run_validation" in src


def test_install_portability_workflow_covers_windows_and_macos():
    workflow = (ROOT / ".github" / "workflows" / "install-portability.yml").read_text(encoding="utf-8")
    assert "windows-latest" in workflow
    assert "macos-latest" in workflow
    assert "install_astt_windows.ps1" in workflow
    assert "install_astt_macos.sh --dry-run --strict-prereq" in workflow
    assert "install_portability_smoke.py" in workflow
    assert "pytest_basetemp_ci" in workflow


def test_install_portability_smoke_has_model_and_web_smoke_hooks():
    src = (ROOT / "scripts" / "install_portability_smoke.py").read_text(encoding="utf-8")
    assert "--level" in src
    assert "--model" in src
    assert "--transcribe-smoke" in src
    assert "scripts/check_health.py" in src
    assert "scripts/check_model.py" in src
    assert "scripts/smoke_transcribe_13s.py" in src
