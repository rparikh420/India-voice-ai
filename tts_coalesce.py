"""
Buffer streaming LLM tokens before Sarvam TTS.

Sarvam's streaming path uses LiveKit's English-oriented sentence tokenizer. Feeding
very small chunks and Latin "." boundaries can produce awkward phrase splits for
Gujarati. Coalescing yields fuller clauses and prefers Gujarati danda (।) as a
flush point when the model uses it.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterable

# Gujarati danda, double danda
_DANDA = "\u0964"
_DOUBLE_DANDA = "\u0965"

# Avoid splitting "3.14" or similar
_DECIMAL_RE = re.compile(r"\d\.\d")


def _latin_sentence_break(buf: str) -> tuple[str, str] | None:
    """If buf has `. ` / `! ` / `? ` that likely ends a Latin sentence, emit prefix."""
    for punct in ".!?":
        start = 0
        while True:
            i = buf.find(punct, start)
            if i == -1:
                break
            if i + 1 < len(buf) and buf[i + 1] != " ":
                start = i + 1
                continue
            if i > 0 and _DECIMAL_RE.search(buf[max(0, i - 2) : i + 2]):
                start = i + 1
                continue
            end = i + 2  # punctuation + space
            return buf[:end].strip(), buf[end:].lstrip()
            start = i + 1
    return None


def _gujarati_danda_break(buf: str) -> tuple[str, str] | None:
    """Emit through first danda when it is followed by whitespace or is at end."""
    i = 0
    while True:
        j_d = buf.find(_DANDA, i)
        j_dd = buf.find(_DOUBLE_DANDA, i)
        candidates = [(j_d, len(_DANDA)), (j_dd, len(_DOUBLE_DANDA))]
        candidates = [(j, ln) for j, ln in candidates if j != -1]
        if not candidates:
            return None
        j, ln = min(candidates, key=lambda x: x[0])
        after = buf[j + ln :]
        if not after or after[0].isspace():
            chunk = buf[: j + ln].strip()
            rest = after.lstrip()
            if chunk:
                return chunk, rest
        i = j + ln


def _length_break(buf: str, max_chunk: int) -> tuple[str, str] | None:
    if len(buf) < max_chunk:
        return None
    window_end = min(len(buf), max_chunk + 24)
    window = buf[:window_end]
    cut = window.rfind(" ")
    if cut < max_chunk // 2:
        cut = max_chunk
    else:
        cut += 1
    emit = buf[:cut].strip()
    rest = buf[cut:]
    if not emit:
        return None
    return emit, rest


def take_coalesced_chunk(buf: str, max_chunk: int) -> tuple[str, str]:
    """
    Pull one speakable prefix off buf, or ('', buf) if more text should accumulate.

    Order: newline, Gujarati danda, Latin sentence + space, then length/word boundary.
    """
    if not buf:
        return "", buf

    if "\n\n" in buf:
        i = buf.index("\n\n")
        return buf[: i + 2].strip(), buf[i + 2 :].lstrip()

    br = _gujarati_danda_break(buf)
    if br:
        return br

    br = _latin_sentence_break(buf)
    if br:
        return br

    br = _length_break(buf, max_chunk)
    if br:
        return br

    return "", buf


def drain_coalesce_buffer(buf: str, max_chunk: int) -> tuple[list[str], str]:
    """Repeatedly take chunks until take_coalesced_chunk yields nothing."""
    out: list[str] = []
    rest = buf
    while True:
        emit, rest = take_coalesced_chunk(rest, max_chunk)
        if not emit:
            break
        out.append(emit)
    return out, rest


async def coalesce_tts_text(
    chunks: AsyncIterable[str],
    *,
    max_chunk_chars: int = 56,
) -> AsyncIterable[str]:
    """Coalesce a raw token stream (e.g. LLM deltas) into fewer TTS pushes."""
    buf = ""
    async for chunk in chunks:
        buf += chunk
        parts, buf = drain_coalesce_buffer(buf, max_chunk_chars)
        for p in parts:
            yield p
    parts, buf = drain_coalesce_buffer(buf, max_chunk_chars)
    for p in parts:
        yield p
    if buf.strip():
        yield buf.strip()
