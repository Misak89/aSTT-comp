"""
Stream pipe — yt-dlp → ffmpeg → PCM float32 chunky.

Bez stahování na disk: yt-dlp získá CDN stream URL, ffmpeg ji dekóduje
a posílá PCM chunky do callbacku nebo jako generátor.

Použití:
    for chunk in stream_audio_chunks(youtube_url, chunk_seconds=0.1):
        # chunk: list[float], sample_rate=16000
        session.add_audio(chunk, 16000)

Požadavky:
    - yt-dlp v PATH nebo jako python modul
    - ffmpeg v PATH
"""
from __future__ import annotations

import subprocess
import sys
import time
from array import array
from pathlib import Path
from typing import Callable, Generator

from packages.common.network_access import ensure_online_allowed


SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2  # int16


def get_youtube_stream_url(youtube_url: str, *, timeout_s: int = 30) -> str:
    """Vrátí přímou CDN audio stream URL přes yt-dlp (bez stahování)."""
    ensure_online_allowed(
        component="packages.ingest.youtube.stream_pipe",
        action="get_youtube_stream_url",
        reason="yt-dlp musí dotázat YouTube pro získání přímé stream URL",
        target=youtube_url,
        details={"timeout_s": timeout_s},
    )
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--get-url",
        "--no-playlist",
        "--format", "bestaudio/best",
        "--quiet",
        youtube_url,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError:
        # zkus jako binárku
        cmd[0] = "yt-dlp"
        cmd.pop(1)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)

    if proc.returncode != 0:
        raise RuntimeError(
            f"yt-dlp --get-url selhalo pro {youtube_url}: {(proc.stderr or '')[:500]}"
        )

    url = next((line.strip() for line in (proc.stdout or "").splitlines() if line.strip()), None)
    if not url:
        raise RuntimeError(f"yt-dlp vrátilo prázdnou URL pro {youtube_url}")
    return url


def stream_audio_chunks(
    source_url: str,
    *,
    chunk_seconds: float = 0.1,
    sample_rate: int = SAMPLE_RATE,
    max_seconds: float | None = None,
    start_offset_seconds: float = 0.0,
) -> Generator[tuple[list[float], int], None, None]:
    """
    Generátor: yt-dlp stream URL → ffmpeg pipe → PCM float32 chunky.

    Yields: (samples: list[float], sample_rate: int)

    Args:
        source_url:  Přímá CDN URL (z get_youtube_stream_url) nebo cesta k souboru.
        chunk_seconds: Délka každého audio chunku v sekundách.
        sample_rate: Cílový sample rate (výchozí 16000 Hz).
        max_seconds: Maximální délka streamování (None = bez limitu).
        start_offset_seconds: Přeskočit N sekund od začátku.
    """
    chunk_samples = max(1, int(sample_rate * chunk_seconds))
    chunk_bytes = chunk_samples * BYTES_PER_SAMPLE

    if str(source_url).startswith(("http://", "https://")):
        ensure_online_allowed(
            component="packages.ingest.youtube.stream_pipe",
            action="stream_audio_chunks",
            reason="ffmpeg čte audio stream přes HTTP(S)",
            target=str(source_url),
            details={
                "chunk_seconds": chunk_seconds,
                "sample_rate": sample_rate,
                "max_seconds": max_seconds,
                "start_offset_seconds": start_offset_seconds,
            },
        )

    ffmpeg_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
    ]
    if start_offset_seconds > 0:
        ffmpeg_cmd.extend(["-ss", str(start_offset_seconds)])
    if max_seconds is not None:
        ffmpeg_cmd.extend(["-t", str(max_seconds)])

    ffmpeg_cmd.extend([
        "-i", source_url,
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "s16le",   # signed 16-bit little-endian PCM
        "pipe:1",
    ])

    proc = subprocess.Popen(
        ffmpeg_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    buffer = b""
    total_samples_yielded = 0
    max_samples = int(max_seconds * sample_rate) if max_seconds is not None else None

    try:
        while True:
            # Přečti chunk_bytes naráz
            needed = chunk_bytes - len(buffer)
            data = proc.stdout.read(needed) if needed > 0 else b""  # type: ignore[union-attr]

            if not data and proc.poll() is not None:
                # ffmpeg skončil
                break
            if not data:
                time.sleep(0.005)
                continue

            buffer += data
            while len(buffer) >= chunk_bytes:
                piece = buffer[:chunk_bytes]
                buffer = buffer[chunk_bytes:]

                pcm = array("h")
                pcm.frombytes(piece)
                samples = [max(-1.0, min(1.0, s / 32768.0)) for s in pcm]
                total_samples_yielded += len(samples)
                yield samples, sample_rate

                if max_samples is not None and total_samples_yielded >= max_samples:
                    return

        # Zbývající buffer
        if len(buffer) >= BYTES_PER_SAMPLE:
            pcm = array("h")
            pcm.frombytes(buffer[: len(buffer) - (len(buffer) % BYTES_PER_SAMPLE)])
            if pcm:
                samples = [max(-1.0, min(1.0, s / 32768.0)) for s in pcm]
                yield samples, sample_rate

    finally:
        try:
            proc.kill()
        except Exception:
            pass
        proc.wait()


def stream_youtube_audio(
    youtube_url: str,
    *,
    chunk_seconds: float = 0.1,
    sample_rate: int = SAMPLE_RATE,
    max_seconds: float | None = None,
    start_offset_seconds: float = 0.0,
    resolve_timeout_s: int = 30,
) -> Generator[tuple[list[float], int], None, None]:
    """
    YouTube URL → yt-dlp pipe → ffmpeg pipe → PCM float32 chunky.

    Používá přímé propojení yt-dlp stdout → ffmpeg stdin (bez CDN URL),
    aby obešel IP-binding CDN URL (EHOSTUNREACH při přímém ffmpeg).

    Yields: (samples: list[float], sample_rate: int)
    """
    yield from _stream_youtube_piped(
        youtube_url,
        chunk_seconds=chunk_seconds,
        sample_rate=sample_rate,
        max_seconds=max_seconds,
        start_offset_seconds=start_offset_seconds,
    )


def _stream_youtube_piped(
    youtube_url: str,
    *,
    chunk_seconds: float = 0.1,
    sample_rate: int = SAMPLE_RATE,
    max_seconds: float | None = None,
    start_offset_seconds: float = 0.0,
) -> Generator[tuple[list[float], int], None, None]:
    """yt-dlp -o - | ffmpeg -i pipe:0 → PCM s16le chunks."""
    ensure_online_allowed(
        component="packages.ingest.youtube.stream_pipe",
        action="_stream_youtube_piped",
        reason="yt-dlp streamuje audio data z YouTube do ffmpeg",
        target=youtube_url,
        details={
            "chunk_seconds": chunk_seconds,
            "sample_rate": sample_rate,
            "max_seconds": max_seconds,
            "start_offset_seconds": start_offset_seconds,
        },
    )
    chunk_samples = max(1, int(sample_rate * chunk_seconds))
    chunk_bytes = chunk_samples * BYTES_PER_SAMPLE

    ytdlp_cmd = [
        sys.executable, "-m", "yt_dlp",
        "--quiet", "--no-playlist",
        "--format", "bestaudio/best",
        "-o", "-",
        youtube_url,
    ]
    ffmpeg_cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if start_offset_seconds > 0:
        ffmpeg_cmd.extend(["-ss", str(start_offset_seconds)])
    if max_seconds is not None:
        ffmpeg_cmd.extend(["-t", str(max_seconds)])
    ffmpeg_cmd.extend(["-i", "pipe:0", "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "pipe:1"])

    ytdlp_proc = subprocess.Popen(ytdlp_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    ffmpeg_proc = subprocess.Popen(
        ffmpeg_cmd,
        stdin=ytdlp_proc.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    ytdlp_proc.stdout.close()  # type: ignore[union-attr]  # ffmpeg owns the pipe

    buffer = b""
    total_samples_yielded = 0
    max_samples = int(max_seconds * sample_rate) if max_seconds is not None else None

    try:
        while True:
            needed = chunk_bytes - len(buffer)
            data = ffmpeg_proc.stdout.read(needed) if needed > 0 else b""  # type: ignore[union-attr]

            if not data and ffmpeg_proc.poll() is not None:
                break
            if not data:
                time.sleep(0.005)
                continue

            buffer += data
            while len(buffer) >= chunk_bytes:
                piece = buffer[:chunk_bytes]
                buffer = buffer[chunk_bytes:]
                pcm = array("h")
                pcm.frombytes(piece)
                samples = [max(-1.0, min(1.0, s / 32768.0)) for s in pcm]
                total_samples_yielded += len(samples)
                yield samples, sample_rate
                if max_samples is not None and total_samples_yielded >= max_samples:
                    return

        if len(buffer) >= BYTES_PER_SAMPLE:
            pcm = array("h")
            pcm.frombytes(buffer[: len(buffer) - (len(buffer) % BYTES_PER_SAMPLE)])
            if pcm:
                samples = [max(-1.0, min(1.0, s / 32768.0)) for s in pcm]
                yield samples, sample_rate

    finally:
        try:
            ffmpeg_proc.kill()
        except Exception:
            pass
        try:
            ytdlp_proc.kill()
        except Exception:
            pass
        ffmpeg_proc.wait()
        ytdlp_proc.wait()


def stream_mic_audio(
    *,
    chunk_seconds: float = 0.1,
    sample_rate: int = SAMPLE_RATE,
    device: int | str | None = None,
    stop_event=None,  # threading.Event — pokud nastaven, zastaví streaming
) -> Generator[tuple[list[float], int], None, None]:
    """
    Mikrofon → PCM float32 chunky přes sounddevice.

    Yields: (samples: list[float], sample_rate: int)

    Args:
        chunk_seconds: Délka každého audio chunku.
        sample_rate: Sample rate (16000 doporučeno pro STT).
        device: Index nebo název audio zařízení (None = výchozí).
        stop_event: threading.Event — streamování se zastaví když je nastaven.
    """
    try:
        import sounddevice as sd  # type: ignore[import-not-found]
        import numpy as np
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Python package sounddevice není nainstalován. "
            "Instalace: pip install sounddevice"
        ) from exc

    chunk_size = max(1, int(sample_rate * chunk_seconds))
    stream = sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        blocksize=chunk_size,
        device=device,
    )

    with stream:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            data, overflowed = stream.read(chunk_size)
            samples = data[:, 0].tolist()  # mono, jako list[float]
            yield samples, sample_rate
