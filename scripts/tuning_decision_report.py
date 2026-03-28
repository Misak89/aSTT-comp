#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
TUNING_ROOT = ROOT / "runtime" / "tuning"


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
    success_rate: float | None
    repro_runs_ok: int
    repro_wer_ci_width: float | None
    score: float
    score_breakdown: dict[str, float]


def _load_status(job_id: str) -> dict[str, Any]:
    status_path = TUNING_ROOT / job_id / "status.json"
    if not status_path.exists():
        raise FileNotFoundError(f"status.json not found: {status_path}")
    return json.loads(status_path.read_text(encoding="utf-8"))


def _repro_map(status: dict[str, Any]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for item in status.get("reproducibility") or []:
        if isinstance(item, dict) and isinstance(item.get("seed_trial_idx"), int):
            out[int(item["seed_trial_idx"])] = item
    return out


def _calc_score(
    *,
    wer: float,
    rtf: float,
    perceived_delay_s: float | None,
    latency_quality: str | None,
    success_rate: float | None,
    repro_runs_ok: int,
    repro_wer_ci_width: float | None,
    prefer_non_proxy: bool,
    has_non_proxy: bool,
) -> tuple[float, dict[str, float]]:
    """
    Lower score is better.
    Base is WER plus penalties for non-realtime runs, instability, proxy latency and CI uncertainty.
    """
    parts: dict[str, float] = {}
    parts["wer"] = wer

    # RTF > 1 je pro live mic problem.
    parts["rtf_penalty"] = max(0.0, rtf - 1.0) * 0.30

    # Penalty for lower trial success rate.
    sr = success_rate if success_rate is not None else 1.0
    parts["stability_penalty"] = max(0.0, 1.0 - sr) * 0.35

    # Delay is secondary but important for UX.
    delay = perceived_delay_s if perceived_delay_s is not None else 0.0
    parts["delay_penalty"] = min(max(0.0, delay), 20.0) * 0.002

    # Proxy latency is weaker evidence for final decision.
    if prefer_non_proxy and has_non_proxy and latency_quality == "proxy_offline":
        parts["proxy_penalty"] = 0.08
    else:
        parts["proxy_penalty"] = 0.0

    # Reproducibility: wider CI95 means higher uncertainty.
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
) -> tuple[list[Candidate], list[Candidate]]:
    results = status.get("results") or []
    base = [
        r for r in results
        if not r.get("is_repeat")
        and not r.get("error")
        and isinstance(r.get("wer"), (int, float))
        and isinstance(r.get("rtf"), (int, float))
    ]
    has_non_proxy = any((r.get("latency_quality") or "") != "proxy_offline" for r in base)
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
        perceived_delay_s = float(r["perceived_delay_s"]) if isinstance(r.get("perceived_delay_s"), (int, float)) else None
        latency_ms = float(r["latency_ms"]) if isinstance(r.get("latency_ms"), (int, float)) else None

        score, score_breakdown = _calc_score(
            wer=wer,
            rtf=rtf,
            perceived_delay_s=perceived_delay_s,
            latency_quality=latency_quality,
            success_rate=success_rate,
            repro_runs_ok=repro_runs_ok,
            repro_wer_ci_width=repro_wer_ci_width,
            prefer_non_proxy=prefer_non_proxy,
            has_non_proxy=has_non_proxy,
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
        and (not (prefer_non_proxy and has_non_proxy) or c.latency_quality != "proxy_offline")
    ]

    candidates.sort(key=lambda c: (c.score, c.wer, c.rtf))
    strict.sort(key=lambda c: (c.score, c.wer, c.rtf))
    return strict, candidates


def _fmt(v: float | None, scale: float = 1.0, suffix: str = "", digits: int = 2) -> str:
    if v is None:
        return "-"
    return f"{v * scale:.{digits}f}{suffix}"


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
        "success_rate": c.success_rate,
        "repro_runs_ok": c.repro_runs_ok,
        "repro_wer_ci_width": c.repro_wer_ci_width,
        "score": c.score,
        "score_breakdown": c.score_breakdown,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Final recommendation from one completed tuning job.")
    parser.add_argument("--job-id", required=True, help="Tuning job id.")
    parser.add_argument("--min-success-rate", type=float, default=0.95, help="Minimum success_rate for strict filter.")
    parser.add_argument("--max-rtf", type=float, default=1.0, help="Maximum RTF for strict live-mic filter.")
    parser.add_argument("--allow-proxy", action="store_true", help="Allow proxy-only latency in strict selection.")
    parser.add_argument("--require-repro-n", type=int, default=3, help="Require at least N runs_ok for strict pool.")
    parser.add_argument("--top", type=int, default=5, help="How many top rows to print.")
    parser.add_argument("--json", action="store_true", help="Emit JSON output.")
    args = parser.parse_args()

    status = _load_status(args.job_id)
    if status.get("status") != "completed":
        print(f"WARN: job {args.job_id} is not completed (status={status.get('status')}).")

    strict, all_candidates = _build_candidates(
        status,
        min_success_rate=float(args.min_success_rate),
        max_rtf=float(args.max_rtf),
        prefer_non_proxy=not args.allow_proxy,
        require_repro_n=max(1, int(args.require_repro_n)),
    )
    chosen_pool = strict if strict else all_candidates
    if not chosen_pool:
        print("FAIL: no valid non-repeat candidates found.")
        return 2
    best = chosen_pool[0]

    if args.json:
        payload = {
            "job_id": args.job_id,
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
            "require_repro_n": max(1, int(args.require_repro_n)),
            "repro_validation": status.get("repro_validation"),
            "selected_pool": "strict" if strict else "fallback_all",
            "best": _candidate_to_dict(best),
            "top": [_candidate_to_dict(c) for c in chosen_pool[: max(1, int(args.top))]],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"DECISION REPORT job={args.job_id}")
    print(
        f"status={status.get('status')} hardware_profile={status.get('hardware_profile') or 'n/a'} "
        f"cap={status.get('constraints_profile') or 'none'} "
        f"(cores {status.get('constraints_cpu_cores') if status.get('constraints_cpu_cores') is not None else '-'} "
        f"RAM {status.get('constraints_ram_limit_mb') if status.get('constraints_ram_limit_mb') is not None else '-'}MB "
        f"prio {status.get('constraints_priority') or '-'}) "
        f"load={status.get('load_profile') or 'none'} "
        f"(CPU {status.get('load_cpu_target_pct') if status.get('load_cpu_target_pct') is not None else '-'}% "
        f"RAM {status.get('load_ram_target_pct') if status.get('load_ram_target_pct') is not None else '-'}%) "
        f"pool={'strict' if strict else 'fallback_all'} repro_n>={max(1, int(args.require_repro_n))}"
    )
    rv = status.get("repro_validation") or {}
    if rv:
        print(
            f"repro_validation: required_n={rv.get('required_n')} checked_top_k={rv.get('checked_top_k')} "
            f"passed={rv.get('passed')} missing={rv.get('missing_trial_idxs') or []}"
        )
    print(
        f"BEST trial #{best.trial_idx} model={best.model_id} "
        f"WER={_fmt(best.wer, 100, '%')} WERsoft={_fmt(best.wer_soft, 100, '%')} "
        f"RTF={_fmt(best.rtf, 1, '', 3)} delay={_fmt(best.perceived_delay_s, 1, 's', 2)} "
        f"success={_fmt(best.success_rate, 100, '%', 1)} lat_q={best.latency_quality or 'n/a'} "
        f"repro_n={best.repro_runs_ok} ci95_w={_fmt(best.repro_wer_ci_width, 100, '%', 2)} score={best.score:.4f}"
    )
    print(f"params={json.dumps(best.params, ensure_ascii=False, sort_keys=True)}")
    print("")
    print(f"TOP {max(1, int(args.top))}:")
    for i, c in enumerate(chosen_pool[: max(1, int(args.top))], start=1):
        print(
            f"{i:02d}. trial={c.trial_idx} WER={_fmt(c.wer, 100, '%')} "
            f"RTF={_fmt(c.rtf, 1, '', 3)} delay={_fmt(c.perceived_delay_s, 1, 's', 2)} "
            f"succ={_fmt(c.success_rate, 100, '%', 1)} lat_q={c.latency_quality or 'n/a'} "
            f"repro_n={c.repro_runs_ok} score={c.score:.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
