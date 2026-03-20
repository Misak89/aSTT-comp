"""
Smoke testy pro scripts/ — ověří že skripty existují a jdou importovat.
Nevyžadují běžící backend ani modely.
"""
from pathlib import Path

SCRIPTS = Path(__file__).parent.parent.parent / "scripts"


def test_check_health_exists():
    assert (SCRIPTS / "check_health.py").exists()


def test_preflight_exists():
    assert (SCRIPTS / "preflight.py").exists()


def test_check_model_exists():
    assert (SCRIPTS / "check_model.py").exists()


def test_copy_subtitles_exists():
    assert (SCRIPTS / "copy_subtitles.py").exists()


def test_preflight_importable():
    """Preflight musí jít importovat bez psutil (graceful degradace)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("preflight", SCRIPTS / "preflight.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "main")
    assert hasattr(mod, "sample_cpu_ram")
