#!/usr/bin/env python
"""
Preflight check: ověří HW podmínky před benchmarkem.
Spusť před každým benchmarkem — pokud selže, výsledky mohou být nespolehlivé.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.common.console_io import configure_console_io

configure_console_io()

# psutil je optional — graceful degradace
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

CPU_WARN_THRESHOLD = 20.0   # % — pokud systém zatíženější, varování
RAM_WARN_FREE_MB = 2048     # MB — pokud méně volné RAM, varování


def sample_cpu_ram(interval_s: float = 2.0, samples: int = 2) -> tuple[float, float, float]:
    """Vrátí (cpu_avg, ram_used_mb, ram_free_mb) ze dvou vzorků."""
    if not HAS_PSUTIL:
        return 0.0, 0.0, 0.0
    cpu_readings = []
    for i in range(samples):
        cpu_readings.append(psutil.cpu_percent(interval=interval_s))
    ram = psutil.virtual_memory()
    return (
        sum(cpu_readings) / len(cpu_readings),
        ram.used / 1024 / 1024,
        ram.available / 1024 / 1024,
    )


def get_cpu_temp() -> float | None:
    """Pokusí se číst teplotu CPU. Vrátí None pokud není dostupná."""
    if not HAS_PSUTIL:
        return None
    try:
        temps = psutil.sensors_temperatures()
        if not temps:
            return None
        for key in ("coretemp", "k10temp", "acpitz", "cpu_thermal"):
            if key in temps:
                return temps[key][0].current
        return next(iter(temps.values()))[0].current
    except Exception:
        return None


def check_heavy_processes() -> list[str]:
    """Vrátí procesy s CPU > 10% (mimo samotný Python skript)."""
    if not HAS_PSUTIL:
        return []
    heavy = []
    self_pid = psutil.Process().pid
    for proc in psutil.process_iter(["pid", "name", "cpu_percent"]):
        try:
            if proc.info["pid"] == self_pid:
                continue
            cpu = proc.info["cpu_percent"] or 0
            if cpu > 10.0:
                heavy.append(f"{proc.info['name']} ({cpu:.0f}%)")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return heavy


def main(dry_run: bool = False) -> int:
    print("=== Preflight check ===\n")
    ok = True

    if not HAS_PSUTIL:
        print("WARN  psutil není nainstalovaný — přeskočeno HW měření")
        print("      pip install psutil")
        return 0

    # CPU + RAM vzorky
    print("Vzorkuji CPU a RAM (2× s 2s intervaly)...")
    cpu_avg, ram_used_mb, ram_free_mb = sample_cpu_ram(interval_s=2.0, samples=2)

    # CPU
    if cpu_avg > CPU_WARN_THRESHOLD:
        print(f"WARN  CPU zatížení: {cpu_avg:.1f}% (threshold: {CPU_WARN_THRESHOLD}%)")
        print("      Výsledky benchmarku mohou být ovlivněny jinými procesy.")
        ok = False
    else:
        print(f"OK    CPU idle: {cpu_avg:.1f}%")

    # RAM
    if ram_free_mb < RAM_WARN_FREE_MB:
        print(f"WARN  Volná RAM: {ram_free_mb:.0f} MB (doporučeno: >{RAM_WARN_FREE_MB} MB)")
        ok = False
    else:
        print(f"OK    Volná RAM: {ram_free_mb:.0f} MB  (použito: {ram_used_mb:.0f} MB)")

    # Teplota
    temp = get_cpu_temp()
    if temp is not None:
        if temp > 85:
            print(f"WARN  CPU teplota: {temp:.0f}°C — hrozí thermal throttling")
            ok = False
        else:
            print(f"OK    CPU teplota: {temp:.0f}°C")
    else:
        print("INFO  CPU teplota: nedostupná (Windows — může vyžadovat admin nebo HW sensor)")

    # Těžké procesy
    heavy = check_heavy_processes()
    if heavy:
        print(f"WARN  Zatížené procesy: {', '.join(heavy[:5])}")
        ok = False
    else:
        print("OK    Žádné těžké procesy na pozadí")

    print()
    if ok:
        print("PASS  System je cisty -- benchmark lze spustit")
        return 0
    else:
        print("WARN  Podminky nejsou idealni -- benchmark lze spustit, oznacte vysledky jako 'conditions_clean: false'")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preflight check před benchmarkem")
    parser.add_argument("--dry-run", action="store_true", help="Jen zkontroluj, neblokuj")
    args = parser.parse_args()
    code = main(dry_run=args.dry_run)
    sys.exit(0 if args.dry_run else code)
