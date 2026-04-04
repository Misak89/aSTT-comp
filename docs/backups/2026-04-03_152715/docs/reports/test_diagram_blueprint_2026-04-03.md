# Test Diagram Blueprint 2026-04-03

Doc-Meta:
- owner: engineering
- status: active
- doc_file: test_diagram_blueprint_2026-04-03.md
- last_updated_utc: 2026-04-03T12:00:26Z
- review_due_utc: 2026-04-15T00:00:00Z

## Ucel
- Dodat jasny podklad pro dva diagramy:
  1. obecny test model (platny napric domenami),
  2. konkretni test model pro aSTT-comp.
- Podklad je navrzen tak, aby po revizi slo rovnou vyrenderovat diagramy bez dalsi analyzy.

## Diagram A - Obecny test model (A/B/C loops + physical gate)
```mermaid
flowchart TD
    C[Change Intake] --> R[Risk + Claim Classification]
    R --> FG{Fidelity Gap?}
    FG -->|No| A[Loop A: Fast deterministic checks]
    FG -->|Yes| A
    A --> AG{A pass?}
    AG -->|No| FIX[Fix + rerun A]
    FIX --> A
    AG -->|Yes| PB{Runtime-critical claim?}
    PB -->|No| DOC[Document scope + limits]
    PB -->|Yes| B[Loop B: Short physical fidelity check]
    B --> BG{B pass?}
    BG -->|No| FIXB[Adjust config/code + rerun A/B]
    FIXB --> A
    BG -->|Yes| HC{High-risk release path?}
    HC -->|No| DOC
    HC -->|Yes| C2[Loop C: Soak/adversarial]
    C2 --> CG{C pass?}
    CG -->|No| FIXC[Fix + targeted rerun]
    FIXC --> A
    CG -->|Yes| DOC
    DOC --> EV[Evidence pack: params/artifacts/provenance]
    EV --> DEC[Decision: baseline/capability/release-gate]
```

### Interpretace A
- Loop A je povinny vzdy (unit/integration/static/security checks).
- Loop B je povinny, pokud je runtime tvrzeni a existuje fidelity gap mezi simulaci a realitou.
- Loop C je povinny pro high-risk/release tvrzeni (stabilita, dlouhy beh, bezpecnostni odolnost).

## Diagram B - aSTT-comp konkretni test model
```mermaid
flowchart TD
    I[Intake: feature/fix/doc change] --> D{Affected domain}
    D --> OM[Online mic path]
    D --> LT[Long transcript + segmentation]
    D --> MON[Dashboard/health/jobs monitoring]

    OM --> A0[Loop A: unit + contract + docs guard]
    LT --> A1[Loop A: segment API/unit + build + docs guard]
    MON --> A2[Loop A: health/dashboard contract]

    A1 --> B1[Loop B: physical UI smoke /library + /transcript]
    B1 --> C1[Loop C: 3x CZ model long-run 30/120/240 min]

    OM --> B0[Loop B: realtime chunk fidelity + RTF]
    B0 --> C0[Loop C: longer session stability]

    MON --> B2[Loop B: runtime state parity check]

    C0 --> G[Release/merge decision]
    C1 --> G
    B2 --> G
```

### Konkretny maping na V6 checkpointy (reuse, bez duplicity)
- V6 CP0-CP6 zustavaji canonical v `docs/tuning_v6_implementacni_plan.md`.
- Tento podklad je diagramova vrstva nad temito checkpointy:
  - CP0-CP2 ~ Loop A,
  - CP3-CP4 ~ Loop B,
  - CP5 ~ Loop C,
  - CP6 ~ release gate.

## Review checklist (pred push)
1. Souhlasi hranice mezi Loop A/B/C s realnou rizikovosti tvrzeni?
2. Je u aSTT-comp diagramu zachovana separace online mic vs transcript flow?
3. Je jasne, kdy je physical gate MUST a kdy je pouze doplnkovy?
4. Odpovida maping CP0-CP6 aktualnimu V6 planu?
