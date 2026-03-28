#!/usr/bin/env python
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import signal
import time

try:
    import psutil
except ModuleNotFoundError:
    psutil = None  # type: ignore[assignment]


def _busy_worker(stop_evt: mp.Event, duty_cycle: float, period_s: float) -> None:
    duty = max(0.0, min(1.0, float(duty_cycle)))
    busy_s = period_s * duty
    idle_s = max(0.0, period_s - busy_s)
    acc = 0.0
    while not stop_evt.is_set():
        if busy_s > 0:
            end_t = time.perf_counter() + busy_s
            while time.perf_counter() < end_t and not stop_evt.is_set():
                acc = (acc * 1.0000001) + 1.0
                if acc > 1e9:
                    acc = 0.0
        if idle_s > 0:
            time.sleep(idle_s)


def _install_signal_handlers(stop_evt: mp.Event) -> None:
    def _handle_signal(_sig, _frame):
        stop_evt.set()

    for signame in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, signame):
            signal.signal(getattr(signal, signame), _handle_signal)


def _run(
    cpu_target_pct: float,
    ram_target_pct: float,
    period_ms: int,
    mem_chunk_mb: int,
    max_alloc_mb: int,
    reserve_free_mb: int,
    ram_tolerance_pct: float,
) -> None:
    stop_evt = mp.Event()
    _install_signal_handlers(stop_evt)

    period_s = max(0.02, float(period_ms) / 1000.0)
    cores = max(1, int(os.cpu_count() or 1))
    desired_cores = max(0.0, min(95.0, float(cpu_target_pct))) / 100.0 * cores
    full_workers = int(desired_cores)
    partial = desired_cores - full_workers

    duty_cycles: list[float] = [1.0 for _ in range(full_workers)]
    if partial > 0.02:
        duty_cycles.append(partial)
    if not duty_cycles and cpu_target_pct > 0:
        duty_cycles.append(min(1.0, max(0.05, desired_cores)))

    workers: list[mp.Process] = []
    for duty in duty_cycles:
        p = mp.Process(target=_busy_worker, args=(stop_evt, duty, period_s))
        p.daemon = True
        p.start()
        workers.append(p)

    chunks: list[bytearray] = []
    chunk_bytes = max(1, int(mem_chunk_mb)) * 1024 * 1024
    max_alloc_bytes = max(0, int(max_alloc_mb)) * 1024 * 1024
    reserve_free_bytes = max(128, int(reserve_free_mb)) * 1024 * 1024

    try:
        while not stop_evt.wait(1.0):
            if psutil is None or ram_target_pct <= 0:
                continue
            try:
                vm = psutil.virtual_memory()
                current_pct = float(vm.percent)
                allocated_bytes = len(chunks) * chunk_bytes
                if current_pct < (ram_target_pct - ram_tolerance_pct):
                    can_alloc = (
                        (allocated_bytes + chunk_bytes) <= max_alloc_bytes
                        and vm.available > (reserve_free_bytes + chunk_bytes)
                    )
                    if can_alloc:
                        chunks.append(bytearray(chunk_bytes))
                elif current_pct > (ram_target_pct + ram_tolerance_pct) and chunks:
                    chunks.pop()
            except Exception:
                pass
    finally:
        stop_evt.set()
        chunks.clear()
        for p in workers:
            p.join(timeout=1.0)
            if p.is_alive():
                try:
                    p.terminate()
                except Exception:
                    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Controlled CPU/RAM load generator for tuning trials.")
    parser.add_argument("--cpu-target-pct", type=float, default=0.0)
    parser.add_argument("--ram-target-pct", type=float, default=0.0)
    parser.add_argument("--period-ms", type=int, default=100)
    parser.add_argument("--mem-chunk-mb", type=int, default=32)
    parser.add_argument("--max-alloc-mb", type=int, default=4096)
    parser.add_argument("--reserve-free-mb", type=int, default=512)
    parser.add_argument("--ram-tolerance-pct", type=float, default=1.5)
    args = parser.parse_args()

    _run(
        cpu_target_pct=max(0.0, min(95.0, float(args.cpu_target_pct))),
        ram_target_pct=max(0.0, min(95.0, float(args.ram_target_pct))),
        period_ms=max(20, int(args.period_ms)),
        mem_chunk_mb=max(1, int(args.mem_chunk_mb)),
        max_alloc_mb=max(128, int(args.max_alloc_mb)),
        reserve_free_mb=max(128, int(args.reserve_free_mb)),
        ram_tolerance_pct=max(0.5, float(args.ram_tolerance_pct)),
    )
    return 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())

