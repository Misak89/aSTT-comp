# Monitoring Baseline Report - 2026-04-03

Doc-Meta:
- owner: engineering
- status: active
- doc_file: monitoring_baseline_2026-04-03.md
- last_updated_utc: 2026-04-03T01:32:42Z
- review_due_utc: 2026-04-15T00:00:00Z

## Scope
- endpoint: `http://127.0.0.1:8012/api/health/processes`
- modes: `fast, slow, full`
- samples per mode: `12`
- interval_ms: `120`

## Summary
| mode | ok/total | mean ms | p50 ms | p95 ms | mean process count | mean zombie count |
|---|---:|---:|---:|---:|---:|---:|
| fast | 12/12 | 596.1 | 597.2 | 751.2 | 4.0 | 0.0 |
| slow | 12/12 | 964.7 | 930.5 | 1297.0 | 8.0 | 0.0 |
| full | 12/12 | 1016.2 | 977.7 | 1210.4 | 8.0 | 0.0 |

## Notes
- This report is baseline telemetry for post-audit v5 tracking.
- Raw per-sample rows are in JSON and JSONL companions.