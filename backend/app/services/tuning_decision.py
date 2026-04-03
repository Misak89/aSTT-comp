from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..config import TUNING_ROOT


@dataclass
class Candidate:
    trial_idx: int
    model_id: str
    params: dict[str, Any]
    wer: float
    wer_soft: float | None
    rtf: float
    perceived_delay_s: float | None
    latency_ms: float | None
    latency_quality: str | None
    latency_lane: str | None
    success_rate: float | None
    repro_runs_ok: int
    repro_wer_ci_width: float | None
    score: float
    score_breakdown: dict[str, float]


def _load_status(job_id: str) -> dict[str, Any] | None:
    status_path = TUNING_ROOT / job_id / "status.json"
    if not status_path.exists():
        return None
    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _repro_map(status: dict[str, Any]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for item in status.get("reproducibility") or []:
        if isinstance(item, dict) and isinstance(item.get("seed_trial_idx"), int):
            out[int(item["seed_trial_idx"])] = item
    return out


_LANE_ORDER = ("strict_live", "probe_online", "batch_proxy", "mixed", "unknown")


def _normalize_latency_lane(
    *,
    latency_lane: str | None,
    latency_quality: str | None,
    perceived_delay_quality: str | None,
) -> str:
    lane = str(latency_lane or "").strip().lower()
    if lane in _LANE_ORDER:
        return lane

    quality = str(latency_quality or "").strip().lower()
    if quality == "measured_live":
        return "strict_live"
    if quality == "probe_online":
        return "probe_online"
    if quality == "proxy_offline":
        return "batch_proxy"
    if quality == "mixed":
        return "mixed"
    if quality == "unknown":
        return "unknown"

    # Legacy fallback: low perceived delay quality means proxy-grade evidence.
    delay_q = str(perceived_delay_quality or "").strip().lower()
    if delay_q == "low":
        return "batch_proxy"
    return "unknown"


def _calc_score(
    *,
    wer: float,
    rtf: float,
    perceived_delay_s: float | None,
    latency_lane: str | None,
    success_rate: float | None,
    repro_runs_ok: int,
    repro_wer_ci_width: float | None,
    prefer_non_proxy: bool,
    has_live_or_probe: bool,
) -> tuple[float, dict[str, float]]:
    parts: dict[str, float] = {}
    parts["wer"] = wer
    parts["rtf_penalty"] = max(0.0, rtf - 1.0) * 0.30

    sr = success_rate if success_rate is not None else 1.0
    parts["stability_penalty"] = max(0.0, 1.0 - sr) * 0.35

    delay = perceived_delay_s if perceived_delay_s is not None else 0.0
    parts["delay_penalty"] = min(max(0.0, delay), 20.0) * 0.002

    lane = str(latency_lane or "unknown")
    if lane == "strict_live":
        parts["lane_penalty"] = 0.0
    elif lane == "probe_online":
        parts["lane_penalty"] = 0.01
    elif lane == "batch_proxy":
        parts["lane_penalty"] = 0.08 if (prefer_non_proxy and has_live_or_probe) else 0.03
    elif lane == "mixed":
        parts["lane_penalty"] = 0.05 if (prefer_non_proxy and has_live_or_probe) else 0.02
    else:
        parts["lane_penalty"] = 0.03 if (prefer_non_proxy and has_live_or_probe) else 0.01

    if repro_wer_ci_width is not None:
        parts["repro_ci_penalty"] = max(0.0, repro_wer_ci_width) * 0.50
        parts["repro_n_penalty"] = 0.01 if repro_runs_ok < 3 else 0.0
    else:
        parts["repro_ci_penalty"] = 0.02
        parts["repro_n_penalty"] = 0.01

    score = sum(parts.values())
    return score, parts


def _build_candidates(
    status: dict[str, Any],
    *,
    min_success_rate: float,
    max_rtf: float,
    prefer_non_proxy: bool,
    require_repro_n: int,
) -> tuple[list[Candidate], list[Candidate], dict[str, int]]:
    results = status.get("results") or []
    base = [
        r for r in results
        if not r.get("is_repeat")
        and not r.get("error")
        and isinstance(r.get("wer"), (int, float))
        and isinstance(r.get("rtf"), (int, float))
    ]
    has_live_or_probe = any(
        _normalize_latency_lane(
            latency_lane=str(r.get("latency_lane") or ""),
            latency_quality=str(r.get("latency_quality") or ""),
            perceived_delay_quality=str(r.get("perceived_delay_quality") or ""),
        )
        in {"strict_live", "probe_online"}
        for r in base
    )
    repro_by_seed = _repro_map(status)

    candidates: list[Candidate] = []
    for r in base:
        trial_idx = int(r["trial_idx"])
        repro = repro_by_seed.get(trial_idx) or {}
        repro_runs_ok = int(repro.get("runs_ok", 0) or 0) if repro else 1
        wer_ci = repro.get("wer") if isinstance(repro.get("wer"), dict) else None
        repro_wer_ci_width = None
        if wer_ci and isinstance(wer_ci.get("ci95_low"), (int, float)) and isinstance(wer_ci.get("ci95_high"), (int, float)):
            repro_wer_ci_width = float(wer_ci["ci95_high"]) - float(wer_ci["ci95_low"])

        wer = float(r["wer"])
        rtf = float(r["rtf"])
        success_rate = float(r["success_rate"]) if isinstance(r.get("success_rate"), (int, float)) else None
        latency_quality = str(r["latency_quality"]) if r.get("latency_quality") else None
        latency_lane = _normalize_latency_lane(
            latency_lane=(str(r["latency_lane"]) if r.get("latency_lane") else None),
            latency_quality=latency_quality,
            perceived_delay_quality=(str(r["perceived_delay_quality"]) if r.get("perceived_delay_quality") else None),
        )
        perceived_delay_s = float(r["perceived_delay_s"]) if isinstance(r.get("perceived_delay_s"), (int, float)) else None
        latency_ms = float(r["latency_ms"]) if isinstance(r.get("latency_ms"), (int, float)) else None

        score, score_breakdown = _calc_score(
            wer=wer,
            rtf=rtf,
            perceived_delay_s=perceived_delay_s,
            latency_lane=latency_lane,
            success_rate=success_rate,
            repro_runs_ok=repro_runs_ok,
            repro_wer_ci_width=repro_wer_ci_width,
            prefer_non_proxy=prefer_non_proxy,
            has_live_or_probe=has_live_or_probe,
        )
        candidates.append(
            Candidate(
                trial_idx=trial_idx,
                model_id=str(r.get("model_id") or ""),
                params=dict(r.get("params") or {}),
                wer=wer,
                wer_soft=(float(r["wer_soft"]) if isinstance(r.get("wer_soft"), (int, float)) else None),
                rtf=rtf,
                perceived_delay_s=perceived_delay_s,
                latency_ms=latency_ms,
                latency_quality=latency_quality,
                latency_lane=latency_lane,
                success_rate=success_rate,
                repro_runs_ok=repro_runs_ok,
                repro_wer_ci_width=repro_wer_ci_width,
                score=score,
                score_breakdown=score_breakdown,
            )
        )

    strict = [
        c for c in candidates
        if c.rtf <= max_rtf
        and (c.success_rate is None or c.success_rate >= min_success_rate)
        and c.repro_runs_ok >= max(1, int(require_repro_n))
    ]

    lane_counts: dict[str, int] = {lane: 0 for lane in _LANE_ORDER}
    for c in candidates:
        lane = str(c.latency_lane or "unknown")
        lane_counts[lane] = lane_counts.get(lane, 0) + 1

    candidates.sort(key=lambda c: (c.score, c.wer, c.rtf))
    strict.sort(key=lambda c: (c.score, c.wer, c.rtf))
    return strict, candidates, lane_counts


def _candidate_to_dict(c: Candidate) -> dict[str, Any]:
    return {
        "trial_idx": c.trial_idx,
        "model_id": c.model_id,
        "params": c.params,
        "wer": c.wer,
        "wer_soft": c.wer_soft,
        "rtf": c.rtf,
        "perceived_delay_s": c.perceived_delay_s,
        "latency_ms": c.latency_ms,
        "latency_quality": c.latency_quality,
        "latency_lane": c.latency_lane,
        "success_rate": c.success_rate,
        "repro_runs_ok": c.repro_runs_ok,
        "repro_wer_ci_width": c.repro_wer_ci_width,
        "score": c.score,
        "score_breakdown": c.score_breakdown,
    }


def _lane_pool(candidates: list[Candidate], lane: str) -> list[Candidate]:
    return [c for c in candidates if str(c.latency_lane or "unknown") == lane]


def _choose_lane_pure_pool(
    *,
    strict_candidates: list[Candidate],
    all_candidates: list[Candidate],
) -> tuple[str, str, list[Candidate]]:
    # Priority order ensures live-valid decisions do not mix with proxy lanes.
    for lane in _LANE_ORDER:
        lane_pool = _lane_pool(strict_candidates, lane)
        if lane_pool:
            if lane == "strict_live":
                return lane, "strict_live", lane_pool
            return lane, f"{lane}_fallback", lane_pool

    for lane in _LANE_ORDER:
        lane_pool = _lane_pool(all_candidates, lane)
        if lane_pool:
            return lane, f"{lane}_fallback", lane_pool

    return "unknown", "fallback_all", []


def get_job_decision_report(
    job_id: str,
    *,
    min_success_rate: float = 0.95,
    max_rtf: float = 1.0,
    allow_proxy: bool = False,
    require_repro_n: int = 3,
    top: int = 5,
) -> dict[str, Any] | None:
    status = _load_status(job_id)
    if status is None:
        return None

    strict, all_candidates, lane_counts = _build_candidates(
        status,
        min_success_rate=float(min_success_rate),
        max_rtf=float(max_rtf),
        prefer_non_proxy=not bool(allow_proxy),
        require_repro_n=max(1, int(require_repro_n)),
    )
    selected_lane, selected_pool, chosen_pool = _choose_lane_pure_pool(
        strict_candidates=strict,
        all_candidates=all_candidates,
    )
    if not chosen_pool:
        return {
            "job_id": job_id,
            "status": status.get("status"),
            "hardware_profile": status.get("hardware_profile"),
            "hardware_note": status.get("hardware_note"),
            "load_profile": status.get("load_profile"),
            "load_cpu_target_pct": status.get("load_cpu_target_pct"),
            "load_ram_target_pct": status.get("load_ram_target_pct"),
            "constraints_profile": status.get("constraints_profile"),
            "constraints_cpu_cores": status.get("constraints_cpu_cores"),
            "constraints_ram_limit_mb": status.get("constraints_ram_limit_mb"),
            "constraints_priority": status.get("constraints_priority"),
            "constraints_ram_mode": status.get("constraints_ram_mode"),
            "require_repro_n": max(1, int(require_repro_n)),
            "repro_validation": status.get("repro_validation"),
            "selected_lane": selected_lane,
            "selected_pool": selected_pool,
            "lane_counts": lane_counts,
            "best": None,
            "top": [],
            "error": "no_valid_candidates",
        }
    best = chosen_pool[0]
    return {
        "job_id": job_id,
        "status": status.get("status"),
        "hardware_profile": status.get("hardware_profile"),
        "hardware_note": status.get("hardware_note"),
        "load_profile": status.get("load_profile"),
        "load_cpu_target_pct": status.get("load_cpu_target_pct"),
        "load_ram_target_pct": status.get("load_ram_target_pct"),
        "constraints_profile": status.get("constraints_profile"),
        "constraints_cpu_cores": status.get("constraints_cpu_cores"),
        "constraints_ram_limit_mb": status.get("constraints_ram_limit_mb"),
        "constraints_priority": status.get("constraints_priority"),
        "constraints_ram_mode": status.get("constraints_ram_mode"),
        "require_repro_n": max(1, int(require_repro_n)),
        "repro_validation": status.get("repro_validation"),
        "selected_lane": selected_lane,
        "selected_pool": selected_pool,
        "lane_counts": lane_counts,
        "best": _candidate_to_dict(best),
        "top": [_candidate_to_dict(c) for c in chosen_pool[: max(1, int(top))]],
        "error": None,
    }
