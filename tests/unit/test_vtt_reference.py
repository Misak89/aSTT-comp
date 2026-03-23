"""
Unit testy pro VTT extrakci (vtt_reference.py).
Používají in-memory VTT soubory v tmp adresáři — nevyžadují reálná videa.
"""
import sys
from pathlib import Path
import tempfile

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from packages.benchmarks.ground_truth.vtt_reference import extract_vtt_clip_text

# Minimální VTT obsah pro testy
_VTT_CONTENT = """\
WEBVTT

00:00:01.000 --> 00:00:04.000
Ahoj, jak se máš?

00:00:05.000 --> 00:00:09.000
Mám se dobře, díky.

00:00:10.000 --> 00:00:15.000
To je skvělé.

00:01:00.000 --> 00:01:05.000
Minutový segment.
"""

_VTT_WITH_TAGS = """\
WEBVTT

00:00:01.000 --> 00:00:04.000
<c>Ahoj</c> světe

00:00:05.000 --> 00:00:09.000
<b>Jak</b> se máš?
"""

_VTT_AUTO_AND_MANUAL = """\
WEBVTT

00:00:01.000 --> 00:00:03.000
Manuální titulky.
"""


def _make_subtitles_dir(tmp: Path, video_id: str, filename: str, content: str) -> Path:
    sub_dir = tmp / video_id
    sub_dir.mkdir(parents=True)
    (sub_dir / filename).write_text(content, encoding="utf-8")
    return tmp


def test_extract_basic_clip():
    with tempfile.TemporaryDirectory() as td:
        root = _make_subtitles_dir(Path(td), "vid1", "cs.vtt", _VTT_CONTENT)
        text = extract_vtt_clip_text("vid1", 0, 10, root)
        assert text is not None
        assert "Ahoj" in text
        assert "Mám se dobře" in text


def test_extract_excludes_outside_range():
    with tempfile.TemporaryDirectory() as td:
        root = _make_subtitles_dir(Path(td), "vid1", "cs.vtt", _VTT_CONTENT)
        # clip 0–9s → minutový segment (60s) nesmí být zahrnut
        text = extract_vtt_clip_text("vid1", 0, 9, root)
        assert text is not None
        assert "Minutový segment" not in text


def test_extract_minute_segment_only():
    with tempfile.TemporaryDirectory() as td:
        root = _make_subtitles_dir(Path(td), "vid1", "cs.vtt", _VTT_CONTENT)
        text = extract_vtt_clip_text("vid1", 59, 70, root)
        assert text is not None
        assert "Minutový segment" in text
        assert "Ahoj" not in text


def test_returns_none_for_missing_video():
    with tempfile.TemporaryDirectory() as td:
        result = extract_vtt_clip_text("neexistuje", 0, 30, Path(td))
        assert result is None


def test_returns_none_for_empty_range():
    with tempfile.TemporaryDirectory() as td:
        root = _make_subtitles_dir(Path(td), "vid1", "cs.vtt", _VTT_CONTENT)
        # clip mimo jakýkoli segment (30–40s)
        result = extract_vtt_clip_text("vid1", 30, 40, root)
        assert result is None


def test_strips_html_tags():
    with tempfile.TemporaryDirectory() as td:
        root = _make_subtitles_dir(Path(td), "vid1", "cs.vtt", _VTT_WITH_TAGS)
        text = extract_vtt_clip_text("vid1", 0, 10, root)
        assert text is not None
        assert "<c>" not in text
        assert "<b>" not in text
        assert "Ahoj" in text
        assert "světe" in text


def test_prefers_manual_over_auto():
    """Manuální titulky mají přednost před .auto. soubory."""
    with tempfile.TemporaryDirectory() as td:
        sub_dir = Path(td) / "vid2"
        sub_dir.mkdir()
        (sub_dir / "cs.auto.vtt").write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nAuto titulky.\n",
            encoding="utf-8",
        )
        (sub_dir / "cs.vtt").write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nManuální titulky.\n",
            encoding="utf-8",
        )
        text = extract_vtt_clip_text("vid2", 0, 5, Path(td))
        assert text == "Manuální titulky."
