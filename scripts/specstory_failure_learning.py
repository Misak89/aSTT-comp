from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SESSION_FILE_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<h>\d{2})-(?P<m>\d{2})-(?P<s>\d{2})Z"
)
EXIT_CODE_RE = re.compile(r"Exit code:\s*(\d+)", re.IGNORECASE)
TRACEBACK_MARKER = "Traceback (most recent call last):"

EXCEPTION_LINE_RE = re.compile(
    r"(?i)\b("
    r"(?:[A-Za-z_][\w.]*)?(?:Error|Exception):|"
    r"Permission denied|Access is denied|ConnectionRefused|"
    r"No such file or directory|timed out|spawn EPERM"
    r")\b"
)

DIRECT_SIGNAL_RE = re.compile(
    r"(?i)("
    r"API Error: Unable to connect to API|"
    r"failed to create root command|"
    r"Permission denied|Access is denied|"
    r"ConnectionRefused|"
    r"ParserError:|"
    r"No such file or directory|"
    r"NotADirectoryError:|FileNotFoundError:|"
    r"ModuleNotFoundError: No module named|"
    r"TypeError:|AttributeError:|RuntimeError:|HTTP Error \d{3}|"
    r"timed out|spawn EPERM"
    r")"
)
RUNTIMEISH_START_RE = re.compile(
    r"(?i)^(Error:|ERR:|API Error:|failed to create root command|ParserError:|"
    r"ModuleNotFoundError:|FileNotFoundError:|NotADirectoryError:|TypeError:|"
    r"AttributeError:|RuntimeError:|HTTP Error \d{3}|ls:|dir:|warning: unable to access|"
    r"/usr/bin/bash:|command timed out)"
)

PATH_RE = re.compile(
    r"([A-Za-z]:\\[^ \t\r\n`\"']+|/[^ \t\r\n`\"']+)",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
HEX_RE = re.compile(r"\b[0-9a-f]{8,}\b", re.IGNORECASE)
NUM_RE = re.compile(r"\b\d+\b")
MULTISPACE_RE = re.compile(r"\s+")


CATEGORY_PROFILES: dict[str, dict[str, Any]] = {
    "command_failure": {
        "keywords": ["exit code: <num>"],
        "impact": 3,
        "blocker": 3,
        "detectability": 5,
        "effort": 2,
        "action": "Inspect stderr for root cause and convert repeated failures into explicit pre-checks.",
    },
    "permissions": {
        "keywords": ["permission denied", "access is denied", "spawn eperm"],
        "impact": 4,
        "blocker": 4,
        "detectability": 5,
        "effort": 2,
        "action": "Check sandbox/escalation rule and filesystem access before rerun.",
    },
    "service_unavailable": {
        "keywords": ["connectionrefused", "unable to connect to api"],
        "impact": 4,
        "blocker": 5,
        "detectability": 4,
        "effort": 2,
        "action": "Verify service health/port first, then retry workflow.",
    },
    "shell_syntax": {
        "keywords": ["parsererror", "missing file specification after redirection operator"],
        "impact": 3,
        "blocker": 4,
        "detectability": 5,
        "effort": 1,
        "action": "Use shell-appropriate syntax (PowerShell vs bash heredoc rules).",
    },
    "path_assumption": {
        "keywords": ["no such file or directory", "filenotfounderror", "notadirectoryerror"],
        "impact": 3,
        "blocker": 3,
        "detectability": 4,
        "effort": 2,
        "action": "Validate path type/existence before operation (file vs directory).",
    },
    "dependency_missing": {
        "keywords": ["modulenotfounderror", "missing .venv python", "whisper-cli nenalezen"],
        "impact": 3,
        "blocker": 4,
        "detectability": 4,
        "effort": 2,
        "action": "Add preflight check for dependency/tool existence before run.",
    },
    "timeout": {
        "keywords": ["timed out", "timeout"],
        "impact": 3,
        "blocker": 3,
        "detectability": 3,
        "effort": 3,
        "action": "Tune timeout + split long operations into smaller validated steps.",
    },
    "api_contract": {
        "keywords": ["http error 422", "unprocessable content", "input should be"],
        "impact": 3,
        "blocker": 3,
        "detectability": 4,
        "effort": 2,
        "action": "Align request schema and enums with backend contract.",
    },
    "unknown": {
        "keywords": [],
        "impact": 2,
        "blocker": 2,
        "detectability": 2,
        "effort": 3,
        "action": "Classify manually and add/prefer repeatable guard condition.",
    },
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_session_time(path: Path) -> datetime | None:
    match = SESSION_FILE_RE.match(path.name)
    if not match:
        return None
    return datetime(
        year=int(match.group("date")[0:4]),
        month=int(match.group("date")[5:7]),
        day=int(match.group("date")[8:10]),
        hour=int(match.group("h")),
        minute=int(match.group("m")),
        second=int(match.group("s")),
        tzinfo=timezone.utc,
    )


def normalize_line(line: str) -> str:
    text = line.strip().lower()
    text = URL_RE.sub("<url>", text)
    text = PATH_RE.sub("<path>", text)
    text = HEX_RE.sub("<hex>", text)
    text = NUM_RE.sub("<num>", text)
    text = MULTISPACE_RE.sub(" ", text)
    return text[:240].strip()


def template_id_from_normalized(normalized: str) -> str:
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def frequency_score(count: int) -> int:
    if count >= 20:
        return 5
    if count >= 10:
        return 4
    if count >= 5:
        return 3
    if count >= 3:
        return 2
    return 1


def priority_label(score: int) -> str:
    if score >= 25:
        return "P0"
    if score >= 18:
        return "P1"
    if score >= 10:
        return "P2"
    return "P3"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def extract_candidates(text: str) -> list[str]:
    lines = text.splitlines()
    results: list[str] = []
    in_code_block = False

    for idx, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("```"):
            in_code_block = not in_code_block
            continue

        # Ignore source/diff-like lines that are not runtime errors.
        if re.match(r"^[+\-]?\s*(raise|except|def |class |import |from |return |if |for |while )", line):
            continue

        exit_match = EXIT_CODE_RE.search(line)
        if exit_match:
            code = int(exit_match.group(1))
            if code != 0:
                results.append(f"Exit code: {code}")

        if in_code_block and TRACEBACK_MARKER in line:
            last_exception: str | None = None
            for look_ahead in range(idx + 1, min(idx + 40, len(lines))):
                probe = lines[look_ahead].strip()
                if EXCEPTION_LINE_RE.search(probe):
                    last_exception = probe
            if last_exception:
                results.append(last_exception)

        if in_code_block and DIRECT_SIGNAL_RE.search(line):
            if RUNTIMEISH_START_RE.search(line) and len(line) <= 220:
                results.append(line)
        elif not in_code_block and re.match(
            r"(?i)^(API Error: Unable to connect to API|failed to create root command|Exit code:\s*[1-9]\d*)",
            line,
        ):
            results.append(line)

    return results


def classify(
    normalized: str,
    learned_rules: list[dict[str, str]],
) -> tuple[str, str]:
    for rule in learned_rules:
        pattern = rule.get("pattern", "")
        if not pattern:
            continue
        try:
            if re.search(pattern, normalized):
                return rule.get("category", "unknown"), "learned"
        except re.error:
            continue

    for category, profile in CATEGORY_PROFILES.items():
        if category == "unknown":
            continue
        if any(keyword in normalized for keyword in profile["keywords"]):
            return category, "static"
    return "unknown", "static"


def promote_learned_rules(
    aggregates: dict[str, dict[str, Any]],
    learned_rules: list[dict[str, str]],
    now: datetime,
    min_promote_count: int,
) -> tuple[list[dict[str, str]], int]:
    existing_templates = {rule.get("source_template_id", "") for rule in learned_rules}
    promoted = 0
    for template_id, row in aggregates.items():
        if row["category"] != "unknown":
            continue
        if row["count"] < min_promote_count:
            continue
        if template_id in existing_templates:
            continue

        snippet = row["normalized"][:90]
        if len(snippet) < 12:
            continue

        learned_rules.append(
            {
                "pattern": re.escape(snippet),
                "category": "unknown",
                "source_template_id": template_id,
                "promoted_at_utc": utc_iso(now),
                "min_count": str(min_promote_count),
            }
        )
        promoted += 1
    return learned_rules, promoted


def build_known_failures_markdown(
    now: datetime,
    aggregates_sorted: list[dict[str, Any]],
    summary: dict[str, Any],
) -> str:
    lines: list[str] = []
    lines.append("# Known Failures")
    lines.append("")
    lines.append("Doc-Meta:")
    lines.append("- owner: engineering")
    lines.append("- status: active")
    lines.append(f"- last_updated_utc: {utc_iso(now)}")
    lines.append("- review_due_utc: 2026-04-15T00:00:00Z")
    lines.append("")
    lines.append("Generated from `.specstory/history/*.md` by `scripts/specstory_failure_learning.py`.")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- scanned_files: {summary['scanned_files']}")
    lines.append(f"- total_events: {summary['total_events']}")
    lines.append(f"- unique_templates: {summary['unique_templates']}")
    lines.append(f"- promoted_rules_this_run: {summary['promoted_rules_this_run']}")
    lines.append("")
    lines.append("## Top Recurring Failures")
    lines.append("")
    lines.append("| Priority | Category | Count | Recent7d | Trend | Template | Action |")
    lines.append("|---|---|---:|---:|---|---|---|")
    visible_rows = [row for row in aggregates_sorted if row["category"] != "command_failure"]
    if not visible_rows:
        visible_rows = aggregates_sorted
    for row in visible_rows[:20]:
        template = row["example"].replace("|", "/")
        action = row["recommended_action"].replace("|", "/")
        lines.append(
            "| {priority} | {category} | {count} | {recent} | {trend} | `{template}` | {action} |".format(
                priority=row["priority_label"],
                category=row["category"],
                count=row["count"],
                recent=row["recent_7d"],
                trend=row["trend"],
                template=template[:90],
                action=action[:90],
            )
        )
    lines.append("")
    lines.append("## Method")
    lines.append("- deterministic extraction: non-zero exit codes + traceback terminal exceptions + strong error signals")
    lines.append("- normalization: paths/urls/ids/numbers masked before template hashing")
    lines.append("- priority score: `4*impact + 3*blocker + 2*frequency + detectability - 2*effort`")
    lines.append("- self-improving rule: unknown templates are auto-promoted to learned rules after repeated occurrences")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate self-improving recurring-failure analytics from .specstory."
    )
    parser.add_argument(
        "--history-dir",
        default=".specstory/history",
        help="Path to specstory history markdown files.",
    )
    parser.add_argument(
        "--stats-file",
        default=".specstory/statistics.json",
        help="Optional specstory statistics file.",
    )
    parser.add_argument(
        "--report-json",
        default="docs/reports/specstory_failures.json",
        help="Output JSON report path.",
    )
    parser.add_argument(
        "--state-json",
        default="docs/reports/specstory_pattern_state.json",
        help="Persistent learning state path.",
    )
    parser.add_argument(
        "--known-failures-md",
        default="docs/KNOWN_FAILURES.md",
        help="Output markdown path for recurring failures.",
    )
    parser.add_argument(
        "--min-promote-count",
        type=int,
        default=3,
        help="Unknown-template occurrence threshold for auto-promoted learned rules.",
    )
    args = parser.parse_args()

    now = utc_now()
    history_dir = Path(args.history_dir)
    stats_file = Path(args.stats_file)
    report_json = Path(args.report_json)
    state_json = Path(args.state_json)
    known_failures_md = Path(args.known_failures_md)

    if not history_dir.exists():
        raise SystemExit(f"History directory not found: {history_dir}")

    history_files = sorted(history_dir.glob("*.md"))
    existing_state = load_json(state_json, default={"learned_rules": [], "templates": {}})
    learned_rules: list[dict[str, str]] = list(existing_state.get("learned_rules", []))
    previous_templates: dict[str, dict[str, Any]] = dict(existing_state.get("templates", {}))

    aggregates: dict[str, dict[str, Any]] = {}
    total_events = 0
    cutoff_7d = now - timedelta(days=7)

    for file_path in history_files:
        session_time = parse_session_time(file_path)
        if session_time is None:
            session_time = now

        text = file_path.read_text(encoding="utf-8", errors="replace")
        candidates = extract_candidates(text)

        for raw in candidates:
            normalized = normalize_line(raw)
            if not normalized:
                continue
            total_events += 1
            template_id = template_id_from_normalized(normalized)
            category, rule_source = classify(normalized, learned_rules)

            row = aggregates.get(template_id)
            if row is None:
                row = {
                    "template_id": template_id,
                    "normalized": normalized,
                    "example": raw[:220],
                    "category": category,
                    "rule_source": rule_source,
                    "count": 0,
                    "recent_7d": 0,
                    "first_seen": utc_iso(session_time),
                    "last_seen": utc_iso(session_time),
                }
                aggregates[template_id] = row

            row["count"] += 1
            if session_time >= cutoff_7d:
                row["recent_7d"] += 1
            if utc_iso(session_time) < row["first_seen"]:
                row["first_seen"] = utc_iso(session_time)
            if utc_iso(session_time) > row["last_seen"]:
                row["last_seen"] = utc_iso(session_time)

    learned_rules, promoted_rules_count = promote_learned_rules(
        aggregates=aggregates,
        learned_rules=learned_rules,
        now=now,
        min_promote_count=args.min_promote_count,
    )

    # Reclassify after promotions.
    for row in aggregates.values():
        category, rule_source = classify(row["normalized"], learned_rules)
        row["category"] = category
        row["rule_source"] = rule_source

        profile = CATEGORY_PROFILES.get(category, CATEGORY_PROFILES["unknown"])
        freq = frequency_score(row["count"])
        score = (
            4 * int(profile["impact"])
            + 3 * int(profile["blocker"])
            + 2 * freq
            + int(profile["detectability"])
            - 2 * int(profile["effort"])
        )
        row["priority_score"] = score
        row["priority_label"] = priority_label(score)
        row["recommended_action"] = profile["action"]

        prev_count = int(previous_templates.get(row["template_id"], {}).get("count", 0))
        if row["count"] > prev_count:
            row["trend"] = "up"
        elif row["count"] < prev_count:
            row["trend"] = "down"
        else:
            row["trend"] = "stable"

    aggregates_sorted = sorted(
        aggregates.values(),
        key=lambda item: (
            {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(item["priority_label"], 9),
            -item["count"],
            item["category"],
        ),
    )

    stats_payload = load_json(stats_file, default={}) if stats_file.exists() else {}
    summary = {
        "scanned_files": len(history_files),
        "total_events": total_events,
        "unique_templates": len(aggregates_sorted),
        "promoted_rules_this_run": promoted_rules_count,
        "learned_rules_total": len(learned_rules),
    }

    report_payload = {
        "generated_at_utc": utc_iso(now),
        "source": {
            "history_dir": str(history_dir).replace("\\", "/"),
            "stats_file": str(stats_file).replace("\\", "/"),
            "stats_sessions_count": len(stats_payload.get("sessions", {}))
            if isinstance(stats_payload, dict)
            else 0,
        },
        "summary": summary,
        "templates": aggregates_sorted,
        "learned_rules": learned_rules,
    }

    state_payload = {
        "generated_at_utc": utc_iso(now),
        "learned_rules": learned_rules,
        "templates": {
            row["template_id"]: {
                "count": row["count"],
                "last_seen": row["last_seen"],
                "category": row["category"],
                "priority_label": row["priority_label"],
            }
            for row in aggregates_sorted
        },
    }

    report_json.parent.mkdir(parents=True, exist_ok=True)
    state_json.parent.mkdir(parents=True, exist_ok=True)
    known_failures_md.parent.mkdir(parents=True, exist_ok=True)

    report_json.write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    state_json.write_text(
        json.dumps(state_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    known_failures_md.write_text(
        build_known_failures_markdown(now, aggregates_sorted, summary),
        encoding="utf-8",
    )

    print(f"specstory-learning: scanned_files={len(history_files)}")
    print(f"specstory-learning: total_events={total_events}")
    print(f"specstory-learning: unique_templates={len(aggregates_sorted)}")
    print(f"specstory-learning: promoted_rules={promoted_rules_count}")
    print(f"specstory-learning: report={report_json}")
    print(f"specstory-learning: known_failures={known_failures_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
