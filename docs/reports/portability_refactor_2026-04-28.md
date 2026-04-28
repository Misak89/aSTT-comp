Doc-Meta:
- owner: engineering
- status: active
- doc_file: docs/reports/portability_refactor_2026-04-28.md
- source_of_truth: false
- last_updated_utc: 2026-04-28T02:38:47Z

# Portability refactor audit 2026-04-28

## Scope

This report records the first scoped portability refactor for installing and running aSTT-comp from different checkout locations on Windows and macOS.

The work focused on path handling, runtime directory consistency, start/stop script safety, and repeatable verification. It was not a full architectural rewrite of every frontend/backend module.

## Facts

- Runtime paths now flow through shared helpers from `packages.common.runtime_paths` in the touched backend and script paths.
- `backend.app.config.RUNTIME_ROOT` now honors `ASTT_RUNTIME_ROOT`.
- Logger, network-access, tuning, worker, benchmark, smoke, and V7 validation scripts no longer hardcode `ROOT / "runtime"` in the refactored paths.
- Unix web wrappers now call `scripts/webctl.py` instead of killing any process found on port `8012`.
- The Library audio-folder placeholder no longer contains a machine-specific `C:\Users\adamf\...` example.
- Added `scripts/portability_audit.py` as a source-level guard for local hardcoded paths, direct runtime bypasses, and unsafe port-kill patterns.

## Verification Performed

| Area | Command/result | Status |
| --- | --- | --- |
| Source path audit | `python scripts/portability_audit.py --fail-on-warn` -> `errors=0 warnings=0` | Pass |
| Python syntax | `python -m compileall backend packages scripts tests` | Pass |
| Targeted tests | `pytest tests/unit/test_runtime_paths.py tests/unit/test_v7_readiness_checklist.py tests/smoke/test_scripts.py -q` -> `13 passed` | Pass |
| Full tests | `pytest tests -q --basetemp=runtime\pytest_basetemp_full` -> `127 passed` | Pass |
| Frontend build | `npm --prefix frontend run build` | Pass |
| Windows installer syntax | PowerShell parser for `scripts/install_astt_windows.ps1` | Pass |
| Runtime override smoke | `ASTT_RUNTIME_ROOT=runtime\_portability_runtime_override` imported backend config and created model/log roots | Pass |
| Local server smoke | `scripts/webctl.py status`, `scripts/check_health.py`, and HTTP `GET /benchmark/mic` | Pass |

## Verification Not Performed

- A physical clean install on a second Windows PC was not executed from this host.
- A physical macOS install was not executed from this host.
- Shell syntax validation with `bash -n` was not possible here because `bash` is not installed in this Windows environment.
- Large model download/install flows were not rerun end-to-end because they are network and storage heavy.

## Critical Findings

1. The codebase already had a large dirty worktree before this pass, including LateMic and UI changes. This refactor intentionally avoided broad rewrites to reduce accidental regression.
2. Test execution on this Windows/Codex environment can be misleading unless pytest gets an explicit clean `--basetemp`; the default temp root had ACL problems.
3. macOS portability is improved structurally, but still needs real validation of `python3`, `npm`, `ffmpeg`, `curl`, `unzip`, optional Homebrew, executable bits, and model binary availability.
4. Some generated documentation and historical logs may still contain absolute local paths by design; the portability audit therefore defaults to source/runtime-control files, not historical artifacts.
5. Model installation remains the biggest cross-machine risk because whisper.cpp/faster-whisper/sherpa/VOSK assets differ in binary availability, size, and CPU support.

## Recommendations

- Add Windows and macOS CI smoke jobs that run `scripts/portability_audit.py --fail-on-warn`, `compileall`, focused unit tests, and frontend build.
- Add an installer dry-run mode for both Windows and macOS that validates tools, paths, writable runtime directories, and expected model locations without downloading large assets.
- Keep runtime data outside source assumptions by using `ASTT_RUNTIME_ROOT` for portable installs and tests.
- Clean or recreate broken pytest temp roots before local full-suite runs, or always use `--basetemp=runtime\pytest_basetemp_full` on Windows.
- Defer broad frontend/backend component decomposition until the portability base is stable and committed.
