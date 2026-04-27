# aSTT-comp — project contract for agents

Doc-Meta:
- owner: engineering
- status: active
- doc_file: AGENTS.md
- source_of_truth: true
- last_updated_utc: 2026-04-02T22:04:43Z
- review_due_utc: 2026-04-15T00:00:00Z

Global Authority (Repository-wide): AGENTS.md is the only top-level Source of Truth for project rules and interpretation. If any document conflicts with AGENTS.md, AGENTS.md prevails.
Mandatory Start (in this repository): README.md -> AGENTS.md -> docs/PLAN_TRACKER.md.

## Why this project exists
- aSTT-comp is a clean rewrite of the old monolithic aSTT-comparison project.
- Goal: maintainable architecture with measurable, reproducible STT evaluation.

## Current product goal (authoritative)
1. Measure and tune STT models for online microphone input.
2. Simulation of microphone transcription is allowed, but must always be clearly labeled as simulation (never labeled as online stream).
3. On `/transcript`, support long-source transcription (about 30-240 minutes).
4. For `/transcript`, acceptable delay is about 30-60 seconds; significantly longer delay is undesirable.
5. Dashboard is a diagnostic panel to understand hardware demands of different STT models and settings.

## Priority order
1. Correct online microphone behavior and measurement quality.
2. Honest mode labeling (online vs simulation vs transcript flow).
3. Stable transcript performance on long inputs.
4. Dashboard diagnostic usefulness.
5. Documentation consistency and traceability.

## Hard rules
- Never present simulated output as online stream output.
- For online mode, feed audio in realtime chunks and track throughput using RTF.
- Use language code `cs` for Czech (`cs-CZ` only when region is required).
- Before any documentation edit, create a timestamp backup at `docs/backups/<YYYY-MM-DD_HHMMSS>/` and keep relative structure (`root/...`, `docs/...`).
- New non-core documentation artifacts use the triplet `MD + JSON + JSONL`; `TXT` is outside triplet (for operational/help files outside `docs/`).
- If behavior changes and affects monitoring/health/jobs/processes, verify Dashboard reflects real runtime state.

## Definition of done
1. Implementation is complete and technically validated (relevant tests/smoke).
2. Dashboard behavior is verified when affected.
3. Updated docs include correct `last_updated_utc` in changed core docs.
4. `docs/session_log.md` contains a concise session entry with impact.
5. Documentation guard requirements remain satisfied.

## Where to read details
- Quick start: `README.md`
- Active plan and status: `docs/PLAN_TRACKER.md`
- Architecture and data flow: `docs/ARCHITECTURE.md`
- Operations and runtime diagnostics: `docs/RUNBOOK.md`
- Workflow and contribution rules: `CONTRIBUTING.md`
- Documentation enforcement contract: `docs/DOCS_GOVERNANCE.md`

## Scope discipline
- Keep AGENTS.md short and directive.
- Put detailed lists (endpoints, long file trees, exhaustive procedures) into specialized docs above.
