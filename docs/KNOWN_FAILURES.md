# Known Failures

Doc-Meta:
- owner: engineering
- status: active
- last_updated_utc: 2026-04-02T03:18:05Z
- review_due_utc: 2026-04-15T00:00:00Z

Generated from `.specstory/history/*.md` by `scripts/specstory_failure_learning.py`.

## Summary
- scanned_files: 40
- total_events: 1111
- unique_templates: 38
- promoted_rules_this_run: 1

## Top Recurring Failures

| Priority | Category | Count | Recent7d | Trend | Template | Action |
|---|---|---:|---:|---|---|---|
| P0 | permissions | 88 | 88 | up | `warning: unable to access 'C:\Users\adamf/.config/git/ignore': Permission denied` | Check sandbox/escalation rule and filesystem access before rerun. |
| P0 | timeout | 19 | 19 | up | `command timed out after 10155 milliseconds` | Tune timeout + split long operations into smaller validated steps. |
| P0 | path_assumption | 10 | 0 | stable | `ls: cannot access 'C:/Users/adamf/OneDrive/Dokumenty/aSTT-comp/.runtime/model_store/': No ` | Validate path type/existence before operation (file vs directory). |
| P0 | path_assumption | 8 | 5 | stable | `ls: cannot access 'C:\Windows\Media\*.wav': No such file or directory` | Validate path type/existence before operation (file vs directory). |
| P0 | path_assumption | 8 | 6 | stable | `dir: cannot access 'C:\Users\adamf\OneDrive\Dokumenty\aSTT-comp\.runtime': No such file or` | Validate path type/existence before operation (file vs directory). |
| P0 | dependency_missing | 7 | 7 | stable | `ModuleNotFoundError: No module named 'packages'` | Add preflight check for dependency/tool existence before run. |
| P0 | dependency_missing | 7 | 7 | stable | `ModuleNotFoundError: No module named 'psutil'` | Add preflight check for dependency/tool existence before run. |
| P0 | service_unavailable | 6 | 2 | stable | `API Error: Unable to connect to API (ConnectionRefused)` | Verify service health/port first, then retry workflow. |
| P0 | permissions | 5 | 5 | up | `failed to create root command: failed to read configuration: open C:\Users\adamf\AppData\R` | Check sandbox/escalation rule and filesystem access before rerun. |
| P0 | path_assumption | 4 | 0 | stable | `FileNotFoundError: [Errno 2] No such file or directory: '.runtime/tuning/tune_20260325_001` | Validate path type/existence before operation (file vs directory). |
| P0 | path_assumption | 3 | 0 | stable | `ls: cannot access 'C:\Users\adamf\OneDrive\Dokumenty\aSTT-comp\.runtime\runs"': No such fi` | Validate path type/existence before operation (file vs directory). |
| P0 | path_assumption | 3 | 0 | stable | `/usr/bin/bash: line 1: node_modules/.bin/tsc: No such file or directory` | Validate path type/existence before operation (file vs directory). |
| P0 | permissions | 3 | 3 | stable | `Error: spawn EPERM` | Check sandbox/escalation rule and filesystem access before rerun. |
| P0 | dependency_missing | 2 | 2 | stable | `ModuleNotFoundError: No module named 'moonshine'` | Add preflight check for dependency/tool existence before run. |
| P0 | dependency_missing | 1 | 0 | stable | `ModuleNotFoundError: No module named 'vosk'` | Add preflight check for dependency/tool existence before run. |
| P0 | dependency_missing | 1 | 1 | stable | `ModuleNotFoundError: No module named 'requests'` | Add preflight check for dependency/tool existence before run. |
| P0 | dependency_missing | 1 | 1 | stable | `ModuleNotFoundError: No module named 'mutagen'` | Add preflight check for dependency/tool existence before run. |
| P0 | permissions | 1 | 1 | up | `+        "keywords": ["permission denied", "access is denied", "spawn eperm"],` | Check sandbox/escalation rule and filesystem access before rerun. |
| P0 | permissions | 1 | 1 | up | `ERROR: Could not install packages due to an OSError: [Errno 13] Permission denied: 'C:\\Us` | Check sandbox/escalation rule and filesystem access before rerun. |
| P1 | unknown | 12 | 12 | up | `AttributeError: 'Wave_write' object has no attribute '_file'` | Classify manually and add/prefer repeatable guard condition. |

## Method
- deterministic extraction: non-zero exit codes + traceback terminal exceptions + strong error signals
- normalization: paths/urls/ids/numbers masked before template hashing
- priority score: `4*impact + 3*blocker + 2*frequency + detectability - 2*effort`
- self-improving rule: unknown templates are auto-promoted to learned rules after repeated occurrences

