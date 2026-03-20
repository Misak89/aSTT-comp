"""
Scénáře: uložená konfigurace benchmarku pro reprodukovatelné měření.
Každý scénář = pojmenovaná sada (videa × modely × nastavení × clip params).
Persistováno v runtime/scenarios/{scenario_id}.json
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import SCENARIOS_ROOT
from ..models.benchmark import Scenario

SCENARIOS_ROOT.mkdir(parents=True, exist_ok=True)


def list_scenarios() -> list[Scenario]:
    out = []
    for f in sorted(SCENARIOS_ROOT.glob("*.json")):
        try:
            out.append(Scenario(**json.loads(f.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return out


def get_scenario(scenario_id: str) -> Optional[Scenario]:
    path = _path(scenario_id)
    if not path.exists():
        return None
    try:
        return Scenario(**json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def save_scenario(scenario: Scenario) -> Scenario:
    if not scenario.created_at:
        scenario = scenario.model_copy(
            update={"created_at": datetime.now(timezone.utc).isoformat()}
        )
    _path(scenario.scenario_id).write_text(
        json.dumps(scenario.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return scenario


def delete_scenario(scenario_id: str) -> bool:
    path = _path(scenario_id)
    if path.exists():
        path.unlink()
        return True
    return False


def _path(scenario_id: str) -> Path:
    # Path traversal ochrana — jen alfanumerické znaky a pomlčky
    safe = "".join(c for c in scenario_id if c.isalnum() or c in "-_")
    return SCENARIOS_ROOT / f"{safe}.json"
