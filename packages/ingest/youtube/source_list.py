from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from .models import OnlineSource

_MARKDOWN_LINK = re.compile(r"\[(?P<label>[^\]]+)\]\((?P<url>https?://[^)]+)\)")
_URL = re.compile(r"https?://[^\s)\]]+")


def parse_source_lines(lines: Iterable[str]) -> list[OnlineSource]:
    """Parse a text list of online sources.

    Supported formats per line:
    - markdown link: [label](https://...)
    - raw URL: https://...
    - text with URL inside line
    """
    sources: list[OnlineSource] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if set(line) == {"-"}:
            continue

        label, url = _extract_label_and_url(line)
        if not url:
            continue

        source_id = f"src-{len(sources) + 1:03d}"
        sources.append(OnlineSource(source_id=source_id, url=url, label=label))

    return sources


def load_sources_from_txt(path: str | Path) -> list[OnlineSource]:
    source_path = Path(path)
    lines = source_path.read_text(encoding="utf-8").splitlines()
    return parse_source_lines(lines)


def _extract_label_and_url(line: str) -> tuple[str, str | None]:
    markdown_match = _MARKDOWN_LINK.search(line)
    if markdown_match:
        return markdown_match.group("label").strip(), markdown_match.group("url").strip()

    url_match = _URL.search(line)
    if not url_match:
        return "", None

    url = url_match.group(0).strip()
    prefix = line[: url_match.start()].strip(" -:\t")
    if prefix:
        label = prefix
    else:
        parsed = urlparse(url)
        label = parsed.netloc or url

    return label, url
