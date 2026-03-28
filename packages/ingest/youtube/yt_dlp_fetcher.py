from __future__ import annotations

import json
import subprocess
from pathlib import Path
import sys
import urllib.error
import urllib.request

from packages.common.network_access import ensure_online_allowed
from packages.ingest.source_resolver import canonicalize_online_source_url

from .models import FetchResult, OnlineSource, StreamResolveResult


class YtDlpFetcher:
    """Downloads media and metadata via yt-dlp."""

    def __init__(self, binary: str | None = None, timeout_seconds: int = 3600) -> None:
        self.binary_cmd = [binary] if binary else [sys.executable, "-m", "yt_dlp"]
        self.timeout_seconds = timeout_seconds

    def fetch(
        self,
        source: OnlineSource,
        output_dir: Path,
        clip_duration_seconds: int,
    ) -> FetchResult:
        ensure_online_allowed(
            component="packages.ingest.youtube.yt_dlp_fetcher",
            action="fetch",
            reason="stažení online media souboru přes yt-dlp",
            target=source.url,
            details={"clip_duration_seconds": clip_duration_seconds},
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        template = str(output_dir / f"{source.source_id}_%(id)s.%(ext)s")
        requested_canonical_url, requested_video_id = canonicalize_online_source_url(source.url)

        cmd = self._build_command(
            source.url,
            template,
            impersonate=False,
        )
        proc = self._run_command(cmd)

        if proc is None:
            return FetchResult(
                source_id=source.source_id,
                url=source.url,
                label=source.label,
                status="failed",
                requested_clip_seconds=clip_duration_seconds,
                error="yt-dlp not found in PATH",
                canonical_url=requested_canonical_url,
                video_id=requested_video_id,
            )

        if proc.returncode != 0 and _looks_like_cloudflare_error(proc.stderr):
            retry_cmd = self._build_command(
                source.url,
                template,
                impersonate=True,
            )
            retry_proc = self._run_command(retry_cmd)
            if retry_proc is not None:
                proc = retry_proc

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            return FetchResult(
                source_id=source.source_id,
                url=source.url,
                label=source.label,
                status="failed",
                requested_clip_seconds=clip_duration_seconds,
                error=stderr[:1000] if stderr else "yt-dlp failed",
                canonical_url=requested_canonical_url,
                video_id=requested_video_id,
            )

        info_path = _latest_info_json(output_dir, source.source_id)
        media_path = _latest_media_file(output_dir, source.source_id)
        subtitle_path = _latest_subtitle_file(output_dir, source.source_id)
        subtitle_error: str | None = None

        title = source.label
        duration_seconds: float | None = None
        info_payload: dict[str, object] | None = None
        canonical_url = requested_canonical_url
        video_id = requested_video_id

        if info_path:
            try:
                payload = json.loads(info_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    info_payload = payload
                    title = payload.get("title") or title
                    duration = payload.get("duration")
                    if isinstance(duration, (int, float)):
                        duration_seconds = float(duration)
                    payload_url = str(payload.get("webpage_url") or payload.get("original_url") or source.url).strip()
                    canonical_url, derived_video_id = canonicalize_online_source_url(payload_url)
                    video_id = str(payload.get("id") or derived_video_id or requested_video_id or "").strip() or None
            except json.JSONDecodeError:
                pass

        if not media_path:
            return FetchResult(
                source_id=source.source_id,
                url=source.url,
                label=source.label,
                status="failed",
                requested_clip_seconds=clip_duration_seconds,
                title=title,
                duration_seconds=duration_seconds,
                error="Download reported success, but media file was not found",
                canonical_url=canonical_url,
                video_id=video_id,
            )

        if info_payload:
            subtitle_path, subtitle_error = _download_subtitle_from_info_json(
                info_payload=info_payload,
                output_dir=output_dir,
                source_id=source.source_id,
            )
        else:
            subtitle_error = "info.json missing, subtitles unavailable"

        return FetchResult(
            source_id=source.source_id,
            url=source.url,
            label=source.label,
            status="downloaded",
            requested_clip_seconds=clip_duration_seconds,
            local_path=str(media_path),
            subtitle_path=str(subtitle_path) if subtitle_path else None,
            title=title,
            duration_seconds=duration_seconds,
            error=subtitle_error,
            canonical_url=canonical_url,
            video_id=video_id,
        )

    def fetch_audio(
        self,
        source: OnlineSource,
        output_dir: Path,
        clip_duration_seconds: int,
    ) -> FetchResult:
        ensure_online_allowed(
            component="packages.ingest.youtube.yt_dlp_fetcher",
            action="fetch_audio",
            reason="stažení audio stopy přes yt-dlp",
            target=source.url,
            details={"clip_duration_seconds": clip_duration_seconds},
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        template = str(output_dir / f"{source.source_id}.%(ext)s")
        requested_canonical_url, requested_video_id = canonicalize_online_source_url(source.url)

        proc = self._run_command(self._build_audio_command(source.url, template))
        if proc is None:
            return FetchResult(
                source_id=source.source_id,
                url=source.url,
                label=source.label,
                status="failed",
                requested_clip_seconds=clip_duration_seconds,
                error="yt-dlp not found in PATH",
                canonical_url=requested_canonical_url,
                video_id=requested_video_id,
            )
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            return FetchResult(
                source_id=source.source_id,
                url=source.url,
                label=source.label,
                status="failed",
                requested_clip_seconds=clip_duration_seconds,
                error=stderr[:1000] if stderr else "yt-dlp audio fetch failed",
                canonical_url=requested_canonical_url,
                video_id=requested_video_id,
            )

        info_path = _latest_exact_info_json(output_dir, source.source_id) or _latest_info_json(output_dir, source.source_id)
        media_path = _latest_exact_media_file(output_dir, source.source_id) or _latest_media_file(output_dir, source.source_id)

        title = source.label
        duration_seconds: float | None = None
        canonical_url = requested_canonical_url
        video_id = requested_video_id

        if info_path:
            try:
                payload = json.loads(info_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    title = payload.get("title") or title
                    duration = payload.get("duration")
                    if isinstance(duration, (int, float)):
                        duration_seconds = float(duration)
                    payload_url = str(payload.get("webpage_url") or payload.get("original_url") or source.url).strip()
                    canonical_url, derived_video_id = canonicalize_online_source_url(payload_url)
                    video_id = str(payload.get("id") or derived_video_id or requested_video_id or "").strip() or None
            except json.JSONDecodeError:
                pass

        if not media_path:
            return FetchResult(
                source_id=source.source_id,
                url=source.url,
                label=source.label,
                status="failed",
                requested_clip_seconds=clip_duration_seconds,
                title=title,
                duration_seconds=duration_seconds,
                error="Audio fetch reported success, but media file was not found",
                canonical_url=canonical_url,
                video_id=video_id,
            )

        return FetchResult(
            source_id=source.source_id,
            url=source.url,
            label=source.label,
            status="downloaded",
            requested_clip_seconds=clip_duration_seconds,
            local_path=str(media_path),
            subtitle_path=None,
            title=title,
            duration_seconds=duration_seconds,
            error=None,
            canonical_url=canonical_url,
            video_id=video_id,
        )

    def resolve_stream(self, source: OnlineSource) -> StreamResolveResult:
        ensure_online_allowed(
            component="packages.ingest.youtube.yt_dlp_fetcher",
            action="resolve_stream",
            reason="vyžádání přímé stream URL přes yt-dlp",
            target=source.url,
        )
        requested_canonical_url, requested_video_id = canonicalize_online_source_url(source.url)
        metadata = self._read_metadata(source.url)
        if metadata is None:
            return StreamResolveResult(
                source_id=source.source_id,
                url=source.url,
                label=source.label,
                status="failed",
                error="yt-dlp not found in PATH",
                canonical_url=requested_canonical_url,
                video_id=requested_video_id,
            )
        if metadata.returncode != 0:
            stderr = (metadata.stderr or "").strip()
            return StreamResolveResult(
                source_id=source.source_id,
                url=source.url,
                label=source.label,
                status="failed",
                error=stderr[:1000] if stderr else "yt-dlp metadata resolution failed",
                canonical_url=requested_canonical_url,
                video_id=requested_video_id,
            )

        title = source.label
        duration_seconds: float | None = None
        canonical_url = requested_canonical_url
        video_id = requested_video_id
        try:
            payload = json.loads(metadata.stdout or "{}")
            if isinstance(payload, dict):
                title = str(payload.get("title") or title)
                duration = payload.get("duration")
                if isinstance(duration, (int, float)):
                    duration_seconds = float(duration)
                payload_url = str(payload.get("webpage_url") or payload.get("original_url") or source.url).strip()
                canonical_url, derived_video_id = canonicalize_online_source_url(payload_url)
                video_id = str(payload.get("id") or derived_video_id or requested_video_id or "").strip() or None
        except json.JSONDecodeError:
            pass

        stream_proc = self._run_command(self._build_stream_resolve_command(source.url))
        if stream_proc is None:
            return StreamResolveResult(
                source_id=source.source_id,
                url=source.url,
                label=source.label,
                status="failed",
                error="yt-dlp not found in PATH",
                canonical_url=canonical_url,
                video_id=video_id,
            )
        if stream_proc.returncode != 0:
            stderr = (stream_proc.stderr or "").strip()
            return StreamResolveResult(
                source_id=source.source_id,
                url=source.url,
                label=source.label,
                status="failed",
                error=stderr[:1000] if stderr else "yt-dlp stream resolution failed",
                canonical_url=canonical_url,
                video_id=video_id,
            )

        stream_url = next((line.strip() for line in (stream_proc.stdout or "").splitlines() if line.strip()), None)
        if not stream_url:
            return StreamResolveResult(
                source_id=source.source_id,
                url=source.url,
                label=source.label,
                status="failed",
                error="yt-dlp stream URL was empty",
                canonical_url=canonical_url,
                video_id=video_id,
            )

        return StreamResolveResult(
            source_id=source.source_id,
            url=source.url,
            label=source.label,
            status="resolved",
            stream_url=stream_url,
            title=title,
            duration_seconds=duration_seconds,
            canonical_url=canonical_url,
            video_id=video_id,
        )

    def _build_command(
        self,
        url: str,
        template: str,
        *,
        impersonate: bool,
    ) -> list[str]:
        cmd = [
            *self.binary_cmd,
            "--no-progress",
            "--newline",
            "--restrict-filenames",
            "--write-info-json",
            "--no-update",
            "--extractor-args",
            "youtube:player_client=android_vr,web",
            "--format",
            "18/22/best[height<=720][ext=mp4]/best[height<=720]/best",
            "--output",
            template,
        ]

        if impersonate:
            cmd.extend(["--extractor-args", "generic:impersonate"])

        cmd.append(url)
        return cmd

    def _build_audio_command(self, url: str, template: str) -> list[str]:
        return [
            *self.binary_cmd,
            "--no-progress",
            "--newline",
            "--restrict-filenames",
            "--write-info-json",
            "--no-update",
            "--extractor-args",
            "youtube:player_client=android_vr,web",
            "--format",
            "bestaudio[ext=m4a]/bestaudio/best",
            "--output",
            template,
            url,
        ]

    def _build_metadata_command(self, url: str) -> list[str]:
        return [
            *self.binary_cmd,
            "--dump-single-json",
            "--skip-download",
            "--no-progress",
            "--no-update",
            "--extractor-args",
            "youtube:player_client=android_vr,web",
            url,
        ]

    def _build_stream_resolve_command(self, url: str) -> list[str]:
        return [
            *self.binary_cmd,
            "--get-url",
            "--no-progress",
            "--no-update",
            "--extractor-args",
            "youtube:player_client=android_vr,web",
            "--format",
            "bestaudio[ext=m4a]/bestaudio/best",
            url,
        ]

    def _read_metadata(self, url: str) -> subprocess.CompletedProcess[str] | None:
        return self._run_command(self._build_metadata_command(url))

    def _run_command(self, cmd: list[str]) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError:
            return None
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(cmd, returncode=124, stdout="", stderr="yt-dlp timeout")


def _latest_info_json(output_dir: Path, source_id: str) -> Path | None:
    candidates = sorted(output_dir.glob(f"{source_id}_*.info.json"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def _latest_exact_info_json(output_dir: Path, source_id: str) -> Path | None:
    candidate = output_dir / f"{source_id}.info.json"
    return candidate if candidate.exists() else None


def _latest_media_file(output_dir: Path, source_id: str) -> Path | None:
    candidates = sorted(
        (
            path
            for path in output_dir.glob(f"{source_id}_*")
            if path.is_file()
            and not path.name.endswith(".info.json")
            and not path.name.lower().endswith(".vtt")
        ),
        key=lambda p: p.stat().st_mtime,
    )
    return candidates[-1] if candidates else None


def _latest_exact_media_file(output_dir: Path, source_id: str) -> Path | None:
    candidates = sorted(
        (
            path
            for path in output_dir.glob(f"{source_id}.*")
            if path.is_file()
            and not path.name.endswith(".info.json")
            and not path.name.lower().endswith(".vtt")
        ),
        key=lambda p: p.stat().st_mtime,
    )
    return candidates[-1] if candidates else None


def _latest_subtitle_file(output_dir: Path, source_id: str) -> Path | None:
    candidates = sorted(
        (
            path
            for path in output_dir.glob(f"{source_id}_*.vtt")
            if path.is_file()
        ),
        key=lambda p: p.stat().st_mtime,
    )
    return candidates[-1] if candidates else None


def _looks_like_cloudflare_error(stderr: str) -> bool:
    text = (stderr or "").lower()
    return "cloudflare" in text or "http error 403" in text


def _download_subtitle_from_info_json(
    *,
    info_payload: dict[str, object],
    output_dir: Path,
    source_id: str,
) -> tuple[str | None, str | None]:
    lang_priority = ["cs", "cs-CZ", "en", "en-US", "en-GB"]
    ext_priority = {"vtt": 0, "srv3": 1, "srt": 2, "json3": 3}

    def select_track(container_name: str) -> dict[str, object] | None:
        container = info_payload.get(container_name)
        if not isinstance(container, dict):
            return None
        for lang in lang_priority:
            tracks = container.get(lang)
            if not isinstance(tracks, list):
                continue
            valid_tracks = [t for t in tracks if isinstance(t, dict) and isinstance(t.get("url"), str)]
            if not valid_tracks:
                continue
            valid_tracks.sort(key=lambda t: ext_priority.get(str(t.get("ext", "")).lower(), 99))
            return valid_tracks[0]
        return None

    track = select_track("subtitles") or select_track("automatic_captions")
    if not track:
        return None, "No preferred subtitle track found in info.json"

    url = str(track.get("url", "")).strip()
    ext = str(track.get("ext", "vtt")).strip().lower() or "vtt"
    lang = str(track.get("name", "unknown")).strip().replace(" ", "_")
    if not url:
        return None, "Subtitle track URL missing"

    out = output_dir / f"{source_id}_subtitle_{lang}.{ext}"
    ensure_online_allowed(
        component="packages.ingest.youtube.yt_dlp_fetcher",
        action="download_subtitle_track",
        reason="stažení titulkového tracku URL z info.json",
        target=url,
        details={"source_id": source_id, "lang": lang, "ext": ext},
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            content = response.read()
        out.write_bytes(content)
    except urllib.error.URLError as exc:
        return None, f"Subtitle download failed: {exc}"
    except Exception as exc:
        return None, f"Subtitle download failed: {exc}"
    return str(out), None
