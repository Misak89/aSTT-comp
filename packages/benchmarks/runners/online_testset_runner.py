from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
import shutil

from packages.ingest.youtube.models import FetchResult
from packages.ingest.youtube.source_list import load_sources_from_txt
from packages.ingest.youtube.yt_dlp_fetcher import YtDlpFetcher


@dataclass(frozen=True)
class OnlineDatasetProfile:
    profile_id: str
    source_file: str
    clip_duration_seconds: int
    max_sources: int
    download_enabled: bool = True


class OnlineTestsetRunner:
    def __init__(self, run_root: str | Path = ".runtime/runs") -> None:
        self.run_root = Path(run_root)

    def prepare(
        self,
        profile_path: str | Path,
        *,
        run_id: str | None = None,
        dry_run: bool = False,
        fetcher: YtDlpFetcher | None = None,
        max_sources_override: int | None = None,
    ) -> Path:
        profile_file = Path(profile_path).resolve()
        profile = load_profile(profile_file)

        source_path = _resolve_path(profile.source_file, profile_file.parent)
        all_sources = load_sources_from_txt(source_path)
        max_sources = max_sources_override if max_sources_override is not None else profile.max_sources
        selected_sources = all_sources[: max(0, max_sources)]

        current_run_id = run_id or datetime.now(UTC).strftime("run_%Y%m%d_%H%M%S")
        run_dir = self.run_root / current_run_id
        raw_dir = run_dir / "sources" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        shutil.copy2(source_path, run_dir / "sources" / "source_list_snapshot.txt")

        used_fetcher = fetcher or YtDlpFetcher()
        records: list[FetchResult] = []

        for source in selected_sources:
            if dry_run or not profile.download_enabled:
                records.append(
                    FetchResult(
                        source_id=source.source_id,
                        url=source.url,
                        label=source.label,
                        status="planned",
                        requested_clip_seconds=profile.clip_duration_seconds,
                    )
                )
                continue

            records.append(
                used_fetcher.fetch(
                    source,
                    raw_dir,
                    clip_duration_seconds=profile.clip_duration_seconds,
                )
            )

        payload = {
            "run_id": current_run_id,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "profile": asdict(profile),
            "max_sources_effective": max_sources,
            "source_file": str(source_path),
            "total_sources_in_file": len(all_sources),
            "selected_source_count": len(selected_sources),
            "results": [entry.to_record() for entry in records],
        }

        manifest_path = run_dir / "online_testset_manifest.json"
        manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        log_path = run_dir / "run.log"
        _write_log(log_path, payload)
        return manifest_path


def load_profile(profile_path: str | Path) -> OnlineDatasetProfile:
    payload = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    return OnlineDatasetProfile(
        profile_id=payload["profile_id"],
        source_file=payload["source_file"],
        clip_duration_seconds=int(payload["clip_duration_seconds"]),
        max_sources=int(payload["max_sources"]),
        download_enabled=bool(payload.get("download_enabled", True)),
    )


def _resolve_path(candidate: str, base_dir: Path) -> Path:
    path = Path(candidate)
    if path.is_absolute():
        return path

    via_base = (base_dir / path).resolve()
    if via_base.exists():
        return via_base

    return (Path.cwd() / path).resolve()


def _write_log(path: Path, payload: dict[str, object]) -> None:
    statuses = {}
    for item in payload["results"]:
        status = item["status"]
        statuses[status] = statuses.get(status, 0) + 1

    lines = [
        f"run_id={payload['run_id']}",
        f"profile={payload['profile']['profile_id']}",
        f"source_count={payload['selected_source_count']}/{payload['total_sources_in_file']}",
        f"max_sources_effective={payload['max_sources_effective']}",
        f"status_counts={statuses}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare_online_testset(
    profile_path: str | Path,
    *,
    run_root: str | Path = ".runtime/runs",
    run_id: str | None = None,
    dry_run: bool = False,
    fetcher: YtDlpFetcher | None = None,
    max_sources_override: int | None = None,
) -> Path:
    runner = OnlineTestsetRunner(run_root=run_root)
    return runner.prepare(
        profile_path,
        run_id=run_id,
        dry_run=dry_run,
        fetcher=fetcher,
        max_sources_override=max_sources_override,
    )
