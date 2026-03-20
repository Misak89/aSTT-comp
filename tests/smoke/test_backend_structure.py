"""
Smoke testy struktury backendu — bez spuštění serveru.
"""
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
BACKEND = ROOT / "backend" / "app"


def test_config_exists():
    assert (BACKEND / "config.py").exists()


def test_main_exists():
    assert (BACKEND / "main.py").exists()


def test_all_routers_exist():
    for name in ("health", "library", "benchmark", "runs"):
        assert (BACKEND / "routers" / f"{name}.py").exists(), f"Chybí router: {name}.py"


def test_all_services_exist():
    for name in ("library_service", "benchmark_service", "results_service"):
        assert (BACKEND / "services" / f"{name}.py").exists(), f"Chybí service: {name}.py"


def test_all_models_exist():
    for name in ("library", "benchmark", "runs"):
        assert (BACKEND / "models" / f"{name}.py").exists(), f"Chybí model: {name}.py"


def test_packages_exist():
    packages = ROOT / "packages"
    for path in (
        "benchmarks/runners/matrix_benchmark_runner.py",
        "benchmarks/metrics/text_metrics.py",
        "benchmarks/ground_truth/vtt_reference.py",
        "adapters/whisper_cpp_runner.py",
        "ingest/youtube/yt_dlp_fetcher.py",
    ):
        assert (packages / path).exists(), f"Chybí package: {path}"
