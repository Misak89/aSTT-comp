from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


def project_root() -> Path:
    # packages/common/runtime_paths.py -> packages/common -> packages -> repo_root
    return Path(__file__).resolve().parents[2]


def runtime_root() -> Path:
    raw = os.environ.get("ASTT_RUNTIME_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    return project_root() / "runtime"


def legacy_runtime_root() -> Path:
    return project_root() / ".runtime"


def runtime_subpath(*parts: str) -> Path:
    return runtime_root().joinpath(*parts)


def runtime_candidates(*parts: str) -> tuple[Path, ...]:
    canonical = runtime_subpath(*parts)
    legacy = legacy_runtime_root().joinpath(*parts)
    if canonical == legacy:
        return (canonical,)
    return (canonical, legacy)


def first_existing_runtime_path(*parts: str) -> Path:
    for candidate in runtime_candidates(*parts):
        if candidate.exists():
            return candidate
    return runtime_subpath(*parts)


def first_existing_runtime_root() -> Path:
    for candidate in (runtime_root(), legacy_runtime_root()):
        if candidate.exists():
            return candidate
    return runtime_root()


def ensure_dirs(paths: Iterable[Path]) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
