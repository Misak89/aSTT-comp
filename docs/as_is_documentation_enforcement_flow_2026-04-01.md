# As-Is Documentation Enforcement Flow (2026-04-01)

Doc-Meta:
- owner: engineering
- status: active
- last_updated_utc: 2026-04-01T19:03:45Z
- review_due_utc: 2026-04-15T00:00:00Z

## Co je hard vs soft
- Hard (nekompromisni): GitHub branch protection + required status check `docs-guard` + PR review pravidla.
- Soft (obejitelne): lokalni `.pre-commit` hooky a cteni instrukcnich dokumentu.
- Advisory: dokumentacni pravidla sama o sobe nic nezastavi, dokud nejsou navazana na hard gate.

```mermaid
flowchart TD
  DEV["Developer / LLM agent"]
  PR["PR opened / updated"]

  subgraph Advisory["Advisory vrstva (sama o sobe neblokuje)"]
    A1["AGENTS.md"]
    A2["CONTRIBUTING.md"]
    A3["docs/DOCS_GOVERNANCE.md"]
    A4["docs/PLAN_TRACKER.md"]
  end

  subgraph LocalSoft["Lokalni soft gate (lze obejit, napr. --no-verify)"]
    L1[".pre-commit-config.yaml"]
    L2["scripts/specstory_failure_learning.py"]
    L3["scripts/supply_chain_guard.py"]
  end

  subgraph CIHard["Server-side hard gate v CI"]
    C1[".github/workflows/docs-guard.yml (job docs-guard)"]
    C2["scripts/verify_docs_guard.py"]
    C3["scripts/supply_chain_guard.py"]
  end

  subgraph RepoHard["GitHub hard gate (branch protection)"]
    R1["Require PR before merge"]
    R2["Required status check: docs-guard"]
    R3["Require approvals + CODEOWNERS review"]
    R4["Enforce admins + no force push"]
  end

  BLOCK["Merge blocked"]
  ALL_OK["All required rules satisfied (AND)"]
  MERGE["Merge allowed"]

  DEV --> A1
  DEV --> A2
  DEV --> A3
  DEV --> A4

  DEV --> L1
  L1 --> L2
  L1 --> L3
  DEV -. "skip local hooks possible" .-> PR

  DEV --> PR
  PR --> C1
  C1 --> C2
  C1 --> C3

  C2 -- fail --> BLOCK
  C3 -- fail --> BLOCK
  C2 -- pass --> R2
  C3 -- pass --> R2

  PR --> R1
  PR --> R3
  PR --> R4

  R1 --> ALL_OK
  R2 --> ALL_OK
  R3 --> ALL_OK
  R4 --> ALL_OK
  ALL_OK --> MERGE

  classDef advisory fill:#eef2ff,stroke:#4f46e5,stroke-width:1px,color:#111827;
  classDef soft fill:#fff7ed,stroke:#ea580c,stroke-width:1px,color:#111827;
  classDef hard fill:#ecfdf5,stroke:#059669,stroke-width:1.5px,color:#111827;
  classDef fail fill:#fef2f2,stroke:#dc2626,stroke-width:1.5px,color:#111827;
  classDef ok fill:#f0fdf4,stroke:#16a34a,stroke-width:1.5px,color:#111827;

  class A1,A2,A3,A4 advisory;
  class L1,L2,L3 soft;
  class C1,C2,C3,R1,R2,R3,R4,ALL_OK hard;
  class BLOCK fail;
  class MERGE ok;
```
