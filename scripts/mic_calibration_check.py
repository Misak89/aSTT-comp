from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.common.console_io import configure_console_io

configure_console_io()

try:
    from backend.app.services.tuning_service import evaluate_mic_calibration as _evaluate_backend
except Exception:
    _evaluate_backend = None


def _evaluate_local(*, rms_dbfs: float, clipping_rate_pct: float, noise_floor_dbfs: float) -> dict:
    thresholds = {
        "rms_min_dbfs": -24.0,
        "rms_max_dbfs": -12.0,
        "clipping_max_pct": 0.1,
        "noise_floor_max_dbfs": -38.0,
    }
    reasons: list[str] = []
    if rms_dbfs < thresholds["rms_min_dbfs"]:
        reasons.append(f"RMS je příliš nízko ({rms_dbfs:.2f} dBFS < {thresholds['rms_min_dbfs']:.2f}).")
    if rms_dbfs > thresholds["rms_max_dbfs"]:
        reasons.append(f"RMS je příliš vysoko ({rms_dbfs:.2f} dBFS > {thresholds['rms_max_dbfs']:.2f}).")
    if clipping_rate_pct > thresholds["clipping_max_pct"]:
        reasons.append(
            f"Clipping je příliš vysoký ({clipping_rate_pct:.3f}% > {thresholds['clipping_max_pct']:.3f}%)."
        )
    if noise_floor_dbfs > thresholds["noise_floor_max_dbfs"]:
        reasons.append(
            f"Noise floor je příliš vysoký ({noise_floor_dbfs:.2f} dBFS > {thresholds['noise_floor_max_dbfs']:.2f} dBFS)."
        )
    return {
        "passed": len(reasons) == 0,
        "reasons": reasons,
        "thresholds": thresholds,
        "metrics": {
            "rms_dbfs": float(rms_dbfs),
            "clipping_rate_pct": float(clipping_rate_pct),
            "noise_floor_dbfs": float(noise_floor_dbfs),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Vyhodnotí kalibraci real mic metrik (RMS / clipping / noise floor)."
    )
    parser.add_argument("--rms-dbfs", type=float, required=True, help="RMS hlasitost v dBFS (doporučeno -24 až -12).")
    parser.add_argument(
        "--clipping-rate-pct",
        type=float,
        required=True,
        help="Podíl clippingu v procentech (doporučeno <= 0.1).",
    )
    parser.add_argument(
        "--noise-floor-dbfs",
        type=float,
        required=True,
        help="Hlukové dno v dBFS (doporučeno <= -38).",
    )
    parser.add_argument("--json", action="store_true", help="Vypíše čistý JSON výstup.")
    args = parser.parse_args()

    if _evaluate_backend is not None:
        report = _evaluate_backend(
            rms_dbfs=args.rms_dbfs,
            clipping_rate_pct=args.clipping_rate_pct,
            noise_floor_dbfs=args.noise_floor_dbfs,
        )
        payload = report.model_dump() if hasattr(report, "model_dump") else dict(report)
    else:
        payload = _evaluate_local(
            rms_dbfs=args.rms_dbfs,
            clipping_rate_pct=args.clipping_rate_pct,
            noise_floor_dbfs=args.noise_floor_dbfs,
        )

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        status = "PASS" if payload["passed"] else "FAIL"
        print(f"[mic_calibration] {status}")
        print(
            "metrics:"
            f" rms_dbfs={payload['metrics']['rms_dbfs']:.2f},"
            f" clipping_rate_pct={payload['metrics']['clipping_rate_pct']:.3f},"
            f" noise_floor_dbfs={payload['metrics']['noise_floor_dbfs']:.2f}"
        )
        print(
            "thresholds:"
            f" rms=[{payload['thresholds']['rms_min_dbfs']:.2f},{payload['thresholds']['rms_max_dbfs']:.2f}],"
            f" clipping<={payload['thresholds']['clipping_max_pct']:.3f},"
            f" noise_floor<={payload['thresholds']['noise_floor_max_dbfs']:.2f}"
        )
        if payload["reasons"]:
            print("reasons:")
            for reason in payload["reasons"]:
                print(f"- {reason}")

    return 0 if payload["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
