from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


UTC_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

ALLOWED_LICENSES = {
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "PSF-2.0",
}
CONDITIONAL_LICENSES = {"MPL-2.0"}
DENIED_LICENSES = {
    "AGPL-3.0",
    "AGPL-3.0-only",
    "AGPL-3.0-or-later",
    "GPL-2.0",
    "GPL-2.0-only",
    "GPL-2.0-or-later",
    "GPL-3.0",
    "GPL-3.0-only",
    "GPL-3.0-or-later",
    "LGPL-2.1",
    "LGPL-2.1-only",
    "LGPL-2.1-or-later",
    "LGPL-3.0",
    "LGPL-3.0-only",
    "LGPL-3.0-or-later",
    "SSPL-1.0",
}
ALLOWED_HOSTS = {
    "github.com",
    "raw.githubusercontent.com",
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "npmjs.com",
}

ENTRY_REQUIRED_KEYS = (
    "component_id",
    "purpose",
    "source_url",
    "source_ref",
    "license_spdx",
    "commercial_use_allowed",
    "adoption_status",
    "integrity",
    "review",
)
ALLOWED_STATUSES = {"planned", "active", "rejected"}


def _is_utc_timestamp(value: str) -> bool:
    return bool(UTC_TS_RE.match(value))


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise ValueError(f"Missing required file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_source_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return f"source_url must use https: {url}"
    if parsed.netloc not in ALLOWED_HOSTS:
        return f"source_url host not in allowlist ({parsed.netloc}): {url}"
    return None


def _validate_intake_register(path: Path) -> list[str]:
    errors: list[str] = []
    data = _load_json(path)

    if "generated_at_utc" not in data or not _is_utc_timestamp(str(data["generated_at_utc"])):
        errors.append(f"{path}: missing or invalid generated_at_utc")

    entries = data.get("entries")
    if not isinstance(entries, list):
        errors.append(f"{path}: entries must be a list")
        return errors

    seen_component_ids: set[str] = set()
    for idx, entry in enumerate(entries):
        prefix = f"{path}: entries[{idx}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix}: entry must be an object")
            continue

        for key in ENTRY_REQUIRED_KEYS:
            if key not in entry:
                errors.append(f"{prefix}: missing key '{key}'")

        component_id = str(entry.get("component_id", "")).strip()
        if not component_id:
            errors.append(f"{prefix}: empty component_id")
        elif component_id in seen_component_ids:
            errors.append(f"{prefix}: duplicate component_id '{component_id}'")
        else:
            seen_component_ids.add(component_id)

        source_url = str(entry.get("source_url", "")).strip()
        if source_url:
            url_error = _validate_source_url(source_url)
            if url_error:
                errors.append(f"{prefix}: {url_error}")

        license_spdx = str(entry.get("license_spdx", "")).strip()
        if not license_spdx:
            errors.append(f"{prefix}: missing license_spdx")
        elif license_spdx in DENIED_LICENSES:
            errors.append(f"{prefix}: denied license for commercial policy: {license_spdx}")
        elif license_spdx not in ALLOWED_LICENSES and license_spdx not in CONDITIONAL_LICENSES:
            errors.append(f"{prefix}: license not in allow/conditional list: {license_spdx}")

        if not isinstance(entry.get("commercial_use_allowed"), bool):
            errors.append(f"{prefix}: commercial_use_allowed must be boolean")
        elif entry.get("adoption_status") == "active" and not entry.get("commercial_use_allowed"):
            errors.append(f"{prefix}: active component must have commercial_use_allowed=true")

        status = str(entry.get("adoption_status", "")).strip()
        if status not in ALLOWED_STATUSES:
            errors.append(f"{prefix}: adoption_status must be one of {sorted(ALLOWED_STATUSES)}")

        integrity = entry.get("integrity")
        if not isinstance(integrity, dict):
            errors.append(f"{prefix}: integrity must be an object")
        else:
            itype = str(integrity.get("type", "")).strip()
            ivalue = str(integrity.get("value", "")).strip()
            if status == "active":
                if itype != "sha256":
                    errors.append(f"{prefix}: active component requires integrity.type='sha256'")
                if not HEX64_RE.match(ivalue):
                    errors.append(f"{prefix}: active component requires 64-char sha256 digest")

        review = entry.get("review")
        if not isinstance(review, dict):
            errors.append(f"{prefix}: review must be an object")
        else:
            reviewed_at = str(review.get("reviewed_at_utc", "")).strip()
            reviewer = str(review.get("reviewer", "")).strip()
            threat_notes = str(review.get("threat_notes", "")).strip()
            if not _is_utc_timestamp(reviewed_at):
                errors.append(f"{prefix}: invalid review.reviewed_at_utc")
            if not reviewer:
                errors.append(f"{prefix}: missing review.reviewer")
            if len(threat_notes) < 8:
                errors.append(f"{prefix}: review.threat_notes too short")

    return errors


def _validate_requirements_txt(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        errors.append(f"Missing {path}")
        return errors
    for idx, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            continue
        low = line.lower()
        if "git+" in low or "://" in low or " @ " in low:
            errors.append(f"{path}:{idx}: direct URL/VCS dependency is not allowed: {line}")
            continue
        if "==" not in line:
            errors.append(f"{path}:{idx}: dependency must be pinned with '==': {line}")
    return errors


def _validate_requirements_lock(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        errors.append(f"Missing {path}")
        return errors
    content = path.read_text(encoding="utf-8")
    hash_count = content.count("--hash=sha256:")
    if hash_count < 20:
        errors.append(f"{path}: expected many sha256 hashes, found only {hash_count}")
    return errors


def _validate_package_json(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        errors.append(f"Missing {path}")
        return errors
    data = json.loads(path.read_text(encoding="utf-8"))
    for section in ("dependencies", "devDependencies"):
        deps = data.get(section, {})
        if not isinstance(deps, dict):
            continue
        for name, version in deps.items():
            v = str(version).strip().lower()
            if v in {"*", "latest"}:
                errors.append(f"{path}: {section}.{name} must not use '{version}'")
            if v.startswith(("file:", "git+", "http:", "https:", "github:")):
                errors.append(f"{path}: {section}.{name} must not use non-registry source '{version}'")
    return errors


def _validate_package_lock(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        errors.append(f"Missing {path}")
        return errors
    content = path.read_text(encoding="utf-8")
    if '"integrity":' not in content:
        errors.append(f"{path}: missing integrity fields")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline supply-chain security guard.")
    parser.add_argument(
        "--intake-register",
        default="docs/reports/oss_intake_register.json",
        help="Path to OSS intake register JSON.",
    )
    parser.add_argument(
        "--requirements",
        default="backend/requirements.txt",
        help="Path to direct Python requirements.",
    )
    parser.add_argument(
        "--requirements-lock",
        default="backend/requirements.lock",
        help="Path to hashed Python lock file.",
    )
    parser.add_argument(
        "--package-json",
        default="frontend/package.json",
        help="Path to npm package manifest.",
    )
    parser.add_argument(
        "--package-lock",
        default="frontend/package-lock.json",
        help="Path to npm lock file.",
    )
    args = parser.parse_args()

    errors: list[str] = []

    try:
        errors.extend(_validate_intake_register(Path(args.intake_register)))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{args.intake_register}: failed to parse/validate ({exc})")

    try:
        errors.extend(_validate_requirements_txt(Path(args.requirements)))
        errors.extend(_validate_requirements_lock(Path(args.requirements_lock)))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Python dependency validation failed: {exc}")

    try:
        errors.extend(_validate_package_json(Path(args.package_json)))
        errors.extend(_validate_package_lock(Path(args.package_lock)))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Node dependency validation failed: {exc}")

    if errors:
        print("supply-chain-guard: FAILED")
        for err in errors:
            print(f"- {err}")
        return 1

    print("supply-chain-guard: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
