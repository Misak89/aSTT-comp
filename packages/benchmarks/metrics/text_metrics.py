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


import re as _re

_NOISE_TAG_RE = _re.compile(r'\[.*?\]')
_PUNCT_RE = _re.compile(r"[^\w\s]")


def normalize_for_wer(text: str) -> str:
    """Agresivnější normalizace: odstraní šumové tagy [hudba], interpunkci, pak NFKC+lower."""
    text = _NOISE_TAG_RE.sub(' ', text or '')
    text = _PUNCT_RE.sub(' ', text)
    return _normalize_text(text)


def word_error_rate_normalized(reference: str, hypothesis: str) -> float:
    """WER s agresivní normalizací (bez interpunkce, bez [tagy])."""
    ref_tokens = _WORD_RE.findall(normalize_for_wer(reference))
    hyp_tokens = _WORD_RE.findall(normalize_for_wer(hypothesis))
    if not ref_tokens:
        return 0.0 if not hyp_tokens else 1.0
    return _levenshtein_distance(ref_tokens, hyp_tokens) / float(len(ref_tokens))


def _alignment_ops(a: list[str], b: list[str]) -> list[tuple[str, "str | None", "str | None"]]:
    """
    Levenshtein s backtrackingem → list of (op, ref_word, hyp_word).
    op: '=' správně | 'S' záměna | 'D' vypuštění | 'I' vložení
    """
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])
    ops: list[tuple[str, "str | None", "str | None"]] = []
    i, j = m, n
    while i > 0 or j > 0:
        if i > 0 and j > 0 and a[i - 1] == b[j - 1] and dp[i][j] == dp[i - 1][j - 1]:
            ops.append(('=', a[i - 1], b[j - 1]))
            i -= 1; j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            ops.append(('S', a[i - 1], b[j - 1]))
            i -= 1; j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            ops.append(('D', a[i - 1], None))
            i -= 1
        else:
            ops.append(('I', None, b[j - 1]))
            j -= 1
    ops.reverse()
    return ops


def match_error_rate(reference: str, hypothesis: str) -> float:
    """MER = (S+D+I) / (H+S+D+I) — není citlivý na dělení slov jako WER."""
    ref_tokens = _tokenize_words(reference)
    hyp_tokens = _tokenize_words(hypothesis)
    if not ref_tokens and not hyp_tokens:
        return 0.0
    ops = _alignment_ops(ref_tokens, hyp_tokens)
    H = sum(1 for op in ops if op[0] == '=')
    errors = sum(1 for op in ops if op[0] != '=')
    total = H + errors
    return errors / total if total > 0 else 0.0


def word_information_lost(reference: str, hypothesis: str) -> float:
    """WIL = 1 - (H/N)*(H/P) kde N=len(ref), P=len(hyp), H=shody."""
    ref_tokens = _tokenize_words(reference)
    hyp_tokens = _tokenize_words(hypothesis)
    N, P = len(ref_tokens), len(hyp_tokens)
    if N == 0 and P == 0:
        return 0.0
    if N == 0 or P == 0:
        return 1.0
    ops = _alignment_ops(ref_tokens, hyp_tokens)
    H = sum(1 for op in ops if op[0] == '=')
    return 1.0 - (H / N) * (H / P)


def word_diff(reference: str, hypothesis: str) -> list[dict]:
    """
    Word-level alignment pro vizualizaci.
    Každý prvek: {'op': '='|'S'|'D'|'I', 'ref': str|None, 'hyp': str|None}
    Vstup je normalizovaný (bez interpunkce, bez tagů).
    """
    ref_tokens = _WORD_RE.findall(normalize_for_wer(reference))
    hyp_tokens = _WORD_RE.findall(normalize_for_wer(hypothesis))
    ops = _alignment_ops(ref_tokens, hyp_tokens)
    return [{'op': op, 'ref': r, 'hyp': h} for op, r, h in ops]


def segment_level_wer(
    segments: list[dict],
    video_id: str,
    clip_start_s: float,
    subtitles_root: "object",  # Path
) -> list[dict]:
    """
    Per-segment WER: zarovná Whisper segmenty s VTT cues podle časových razítek.
    segments: whisper JSON transcription[] s offsets.from/to (v ms, relativní k clip_start)
    Vrátí list dict: {from_ms, to_ms, hyp_text, ref_text, wer, ops_count}
    """
    from packages.benchmarks.ground_truth.vtt_reference import extract_vtt_clip_text
    result = []
    for seg in segments:
        offsets = seg.get("offsets", {})
        from_ms = offsets.get("from", 0)
        to_ms = offsets.get("to", 0)
        if to_ms <= from_ms:
            continue
        hyp_text = str(seg.get("text", "")).strip()
        if not hyp_text:
            continue
        # VTT okno = clip_start + segment offset
        seg_start_s = clip_start_s + from_ms / 1000.0
        seg_end_s   = clip_start_s + to_ms   / 1000.0
        ref_text = extract_vtt_clip_text(video_id, seg_start_s, seg_end_s, subtitles_root)
        seg_wer: "float | None" = None
        if ref_text:
            seg_wer = round(word_error_rate_normalized(ref_text, hyp_text), 4)
        result.append({
            "from_ms": from_ms,
            "to_ms":   to_ms,
            "hyp_text": hyp_text,
            "ref_text": ref_text,
            "wer": seg_wer,
        })
    return result

