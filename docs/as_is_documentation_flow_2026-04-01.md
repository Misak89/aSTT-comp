# As-Is Documentation Flow (2026-04-01)

Doc-Meta:
- owner: engineering
- status: active
- last_updated_utc: 2026-04-01T19:03:45Z
- review_due_utc: 2026-04-15T00:00:00Z

Pro enforcement pohled (hard vs soft gate) viz:
- `docs/as_is_documentation_enforcement_flow_2026-04-01.md`

```mermaid
flowchart LR
  subgraph Inputs["Change Inputs"]
    A1["Code change (backend/frontend/packages/scripts/tests)"]
    A2["Architecture path change (backend/app/services, routers, packages)"]
    A3["Ops path change (web-*, start_web_app*, check_health.py, preflight.py, backend/app/config.py)"]
    A4["Plan/Roadmap docs change (docs/tuning_*, docs/mic_sequence_*, *plan*.md, *roadmap*.md)"]
    A5["Dependency files change (backend/requirements*, frontend/package*.json)"]
    A6["specstory_failure_learning.py changed"]
    A7["supply_chain_guard.py changed"]
  end

  subgraph Docs["Required Docs / Artifacts"]
    D1["docs/session_log.md"]
    D2["docs/ARCHITECTURE.md"]
    D3["docs/RUNBOOK.md"]
    D4["docs/PLAN_TRACKER.md"]
    D5["docs/KNOWN_FAILURES.md"]
    D6["docs/reports/specstory_failures.json"]
    D7["docs/reports/specstory_pattern_state.json"]
    D8["docs/reports/oss_intake_register.json"]
    D9["docs/SECURITY_SUPPLY_CHAIN.md"]
    D10["Core docs Doc-Meta (README, AGENTS, CLAUDE, CONTRIBUTING, PLAN_TRACKER, session_log, ARCH, RUNBOOK)"]
  end

  subgraph Local["Local Automation"]
    L1[".pre-commit-config.yaml"]
    L2["scripts/specstory_failure_learning.py"]
    L3["scripts/supply_chain_guard.py"]
  end

  subgraph CI["CI / PR Gates"]
    C1["scripts/verify_docs_guard.py"]
    C2[".github/workflows/docs-guard.yml"]
    C3["GitHub branch protection + required check + CODEOWNERS"]
  end

  A1 --> D1
  A2 --> D2
  A3 --> D3
  A4 --> D4
  A5 --> D8
  A6 --> D5
  A6 --> D6
  A6 --> D7
  A7 --> D9
  A7 --> D8

  L1 --> L2
  L1 --> L3
  L2 --> D5
  L2 --> D6
  L2 --> D7
  L3 --> D8

  A1 --> C1
  A2 --> C1
  A3 --> C1
  A4 --> C1
  A5 --> C1
  A6 --> C1
  A7 --> C1

  D1 --> C1
  D2 --> C1
  D3 --> C1
  D4 --> C1
  D5 --> C1
  D6 --> C1
  D7 --> C1
  D8 --> C1
  D9 --> C1
  D10 --> C1

  C2 --> C1
  C2 --> L3
  C2 --> C3
```
