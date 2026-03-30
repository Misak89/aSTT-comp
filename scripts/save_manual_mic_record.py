#!/usr/bin/env python
"""
Ulozi rucne zadany MIC zaznam do runtime/mic_sessions/history.

Priklad:
  .venv\\Scripts\\python scripts/save_manual_mic_record.py \
    --model-id whisper_cpp_large_v3_turbo \
    --quality "nekvalitni_prepis" \
    --note "cteno 2-4 radky druheho textu" \
    --metrics-json "{\"rtf\":5.419,\"drop_rate\":0.6637,\"reason_code\":\"backpressure_drop\"}"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.app.services import mic_service


def _parse_metrics(args: argparse.Namespace) -> dict[str, Any]:
    if args.metrics_file:
        path = Path(args.metrics_file)
        return json.loads(path.read_text(encoding="utf-8"))
    if args.metrics_json:
        return json.loads(args.metrics_json)
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Ulozi rucni MIC zaznam.")
    parser.add_argument("--model-id", required=True, help="Model ID, napriklad whisper_cpp_small")
    parser.add_argument("--quality", default="manual_record", help="Kvalitativni tag zaznamu")
    parser.add_argument("--note", default="", help="Poznamka k zaznamu")
    parser.add_argument("--transcript", default="", help="Volitelny prepis")
    parser.add_argument("--source", default="manual_cli", help="Zdroj zaznamu")
    parser.add_argument("--metrics-json", default="", help="JSON string s metrikami")
    parser.add_argument("--metrics-file", default="", help="Cesta na JSON soubor s metrikami")
    args = parser.parse_args()

    metrics = _parse_metrics(args)
    saved = mic_service.save_manual_record(
        model_id=args.model_id,
        metrics=metrics,
        note=args.note,
        quality_assessment=args.quality,
        transcript=args.transcript,
        source=args.source,
    )
    print(json.dumps(saved, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

