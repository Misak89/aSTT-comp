"""
Streaming simulátor — simuluje živý rozhovor.

Místo batch zpracování celého souboru posílá audio v real-time chuncích
(default 200ms), jako by přicházelo z mikrofonu.
Měří time_to_first_text_ms a avg_word_lag_ms.

Použití:
    result = simulate_streaming(audio_path, adapter_fn, chunk_ms=200)
"""
from __future__ import annotations

import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator


@dataclass
class StreamingResult:
    transcript: str = ""
    time_to_first_text_ms: float | None = None  # ms od začátku do prvního slova
    avg_word_lag_ms: float | None = None          # průměrné zpoždění slova za audem
    rtf: float | None = None                      # real-time factor
    chunks_sent: int = 0
    chunks_with_output: int = 0
    word_lags_ms: list[float] = field(default_factory=list)
    duration_seconds: float = 0.0
    wall_seconds: float = 0.0


def read_pcm_chunks(audio_path: Path, chunk_ms: int = 200) -> Iterator[tuple[bytes, float]]:
    """
    Čte WAV soubor a vrací (chunk_bytes, chunk_duration_s) po chunk_ms kouscích.
    Pouze PCM WAV (16kHz, 16bit, mono preferováno).
    """
    with wave.open(str(audio_path), "rb") as wf:
        sample_rate = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        frames_per_chunk = int(sample_rate * chunk_ms / 1000)
        chunk_duration = frames_per_chunk / sample_rate

        while True:
            frames = wf.readframes(frames_per_chunk)
            if not frames:
                break
            yield frames, chunk_duration


def simulate_streaming(
    audio_path: Path,
    adapter_fn: Callable[[bytes, int, int, int], str | None],
    chunk_ms: int = 200,
) -> StreamingResult:
    """
    Simuluje streaming přepis:
    - Posílá audio v chuncích po chunk_ms ms (reálný čas = audio čas → RTF≈1)
    - Měří kdy přišel první výstup textu → time_to_first_text_ms
    - Měří zpoždění každého slova za pozicí v audiu → avg_word_lag_ms
    - Pokud model nestíhá (RTF > 1.0), chunk se stále pošle ale zpoždění roste

    adapter_fn(chunk_bytes, sample_rate, channels, sampwidth) → str | None
    """
    result = StreamingResult()
    audio_path = Path(audio_path)

    if not audio_path.exists():
        return result

    with wave.open(str(audio_path), "rb") as wf:
        sample_rate = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        total_frames = wf.getnframes()
        result.duration_seconds = total_frames / sample_rate

    wall_start = time.perf_counter()
    audio_position_s = 0.0
    transcript_parts: list[str] = []
    first_text_time: float | None = None

    for chunk_bytes, chunk_duration in read_pcm_chunks(audio_path, chunk_ms):
        chunk_start_wall = time.perf_counter()
        result.chunks_sent += 1

        # Simuluj real-time: počkej aby chunk přišel ve správný čas
        elapsed = time.perf_counter() - wall_start
        audio_position_s += chunk_duration
        sleep_needed = audio_position_s - elapsed
        if sleep_needed > 0:
            time.sleep(sleep_needed)

        # Pošli chunk adapteru
        text = adapter_fn(chunk_bytes, sample_rate, n_channels, sampwidth)

        if text and text.strip():
            result.chunks_with_output += 1
            transcript_parts.append(text.strip())

            chunk_end_wall = time.perf_counter()
            lag_ms = (chunk_end_wall - wall_start - audio_position_s) * 1000

            # time_to_first_text = čas od začátku do prvního výstupu
            if first_text_time is None:
                first_text_time = (chunk_end_wall - wall_start) * 1000
                result.time_to_first_text_ms = round(first_text_time, 1)

            # Zpoždění každého nového slova
            words = text.strip().split()
            for _ in words:
                result.word_lags_ms.append(max(0.0, lag_ms))

    result.wall_seconds = time.perf_counter() - wall_start
    result.transcript = " ".join(transcript_parts)

    if result.duration_seconds > 0:
        result.rtf = round(result.wall_seconds / result.duration_seconds, 3)

    if result.word_lags_ms:
        result.avg_word_lag_ms = round(
            sum(result.word_lags_ms) / len(result.word_lags_ms), 1
        )

    return result
