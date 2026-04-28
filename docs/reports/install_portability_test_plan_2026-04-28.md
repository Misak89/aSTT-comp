Doc-Meta:
- owner: engineering
- status: active
- doc_file: docs/reports/install_portability_test_plan_2026-04-28.md
- source_of_truth: false
- last_updated_utc: 2026-04-28T02:55:36Z

# Install Portability Test Plan 2026-04-28

## Goal

Verify that aSTT-comp can be installed and smoke-tested from a clean checkout on Windows and macOS without depending on local absolute paths from the development machine.

## Added Test Layers

| Layer | Implementation | Purpose |
| --- | --- | --- |
| Installer dry-run | `scripts/install_astt_windows.ps1 -DryRun -StrictPrereq`, `scripts/install_astt_macos.sh --dry-run --strict-prereq` | Noninteractive prerequisite, repo layout, and writability validation without downloads or package installs. |
| CI matrix | `.github/workflows/install-portability.yml` | Runs Windows and macOS install checks on GitHub runners. |
| Portable smoke script | `scripts/install_portability_smoke.py` | Shared local/CI smoke runner for audit, runtime override, compileall, frontend build, pytest, web health, and optional model smoke. |
| Unit guard | `tests/unit/test_install_portability.py` | Ensures dry-run hooks, CI workflow, and smoke hooks remain present. |
| Physical clean install | Manual VM/host procedure | Still required for final release confidence, especially model binaries and OS-level audio/ffmpeg behavior. |

## Recommended Commands

Windows dry-run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_astt_windows.ps1 -DryRun -StrictPrereq
```

macOS dry-run:

```bash
bash scripts/install_astt_macos.sh --dry-run --strict-prereq
```

Local CI-equivalent smoke after dependencies are installed:

```bash
python scripts/install_portability_smoke.py --level ci --require-ffmpeg --pytest-basetemp runtime/pytest_basetemp_ci
```

Post-install web smoke:

```bash
python scripts/install_portability_smoke.py --level post-install --skip-frontend-build --skip-pytest
```

Optional model smoke after model files are installed:

```bash
python scripts/install_portability_smoke.py --level post-install --model whisper_cpp_base --transcribe-smoke
```

## Local Evidence

- Windows installer dry-run passed locally.
- `install_portability_smoke.py --level static` passed locally.
- `install_portability_smoke.py --level ci --pytest-basetemp runtime\pytest_basetemp_install_smoke_full` passed locally with `131 passed`.
- `install_portability_smoke.py --level post-install --skip-frontend-build --skip-pytest` passed locally and restored the web server to running state.
- `npm --prefix frontend run build` passed locally; Vite still reports the existing large chunk warning.

## Limits

- macOS dry-run and CI matrix are implemented but not executed on this Windows host.
- GitHub Actions execution is pending until changes are pushed.
- Clean install on a separate Windows or macOS machine is still a manual release gate.
- Model smoke requires installed model files; CI deliberately does not download multi-GB models by default.
