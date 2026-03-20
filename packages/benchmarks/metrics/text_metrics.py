from __future__ import annotations

import re
import unicodedata


_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"\w+", flags=re.UNICODE)


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref_tokens = _tokenize_words(reference)
    hyp_tokens = _tokenize_words(hypothesis)
    if not ref_tokens:
        return 0.0 if not hyp_tokens else 1.0
    return _levenshtein_distance(ref_tokens, hyp_tokens) / float(len(ref_tokens))


def char_error_rate(reference: str, hypothesis: str) -> float:
    ref_chars = list(_normalize_text(reference))
    hyp_chars = list(_normalize_text(hypothesis))
    if not ref_chars:
        return 0.0 if not hyp_chars else 1.0
    return _levenshtein_distance(ref_chars, hyp_chars) / float(len(ref_chars))


def _tokenize_words(text: str) -> list[str]:
    normalized = _normalize_text(text)
    return _WORD_RE.findall(normalized)


def _normalize_text(text: str) -> str:
    # Keep accents; normalize unicode form and collapse whitespace for deterministic scoring.
    norm = unicodedata.normalize("NFKC", text or "")
    norm = norm.lower()
    norm = _WS_RE.sub(" ", norm).strip()
    return norm


def _levenshtein_distance(a: list[str], b: list[str]) -> int:
    if not a:
        return len(b)
    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, token_a in enumerate(a, start=1):
        curr = [i]
        for j, token_b in enumerate(b, start=1):
            cost = 0 if token_a == token_b else 1
            curr.append(
                min(
                    prev[j] + 1,      # deletion
                    curr[j - 1] + 1,  # insertion
                    prev[j - 1] + cost,  # substitution
                )
            )
        prev = curr
    return prev[-1]

