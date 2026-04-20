# V7 readiness checklist (2026-04-20)

Doc-Meta:
- owner: engineering
- status: active
- doc_file: v7_readiness_checklist_2026-04-20.md
- last_updated_utc: 2026-04-20T20:01:12Z
- review_due_utc: 2026-04-21T00:00:00Z

## Scope
- Tento checklist je bod 8 pro `cs-online-mic-orchestrator-v7`: ověření readiness nad reálnými runtime artefakty.
- Běh je evidován jako runtime validace, nikoliv jako náhrada fyzického online mikrofon testu.

## Command
```powershell
.venv\Scripts\python scripts/v7_readiness_checklist.py `
  --sequence-token shared_seq `
  --api-base http://127.0.0.1:8012 `
  --output-json runtime/v7_readiness_latest.json `
  --output-md runtime/v7_readiness_latest.md `
  --output-jsonl runtime/v7_readiness_latest.jsonl
```

## Result
- `overall_pass`: `false`
- `sequence_token`: `shared_seq`
- `failed_checks`:
  - `model_count_range`
  - `all_trials_started`
  - `all_trials_finalized`
  - `latency_available_all_trials`
  - `strict_readiness`
  - `dashboard_runtime_mapping_status`

## Key Evidence
- Trials: `2`
- Model count: `1` (`whisper_cpp_small`) -> požadavek je `3-5`.
- Started/Finalized trials: `0/2` a `0/2`.
- Latency known: `0/2`.
- Event log coverage pro sekvenci: `24` V7 eventů, contract invalid `0`, unknown reason `0`.
- Dashboard runtime mapping endpoint je dostupný, ale vrací status `warn` (`readiness_fail_reports=16`).

## Conclusion
- V7-S5 validace není splněna.
- Blokery jsou reálně v datech, ne v contract parseru:
  - chybí fyzický 3-5 model run v jedné sekvenci,
  - chybí `started/finalized` trial data a latency evidence.
