"""
Parse XML-style tags emitted by the LLM to control speech behaviour.

Supported tags
--------------
<NoInterrupt>...</NoInterrupt>
    Speech inside this tag cannot be interrupted by the user.
    Use for critical confirmations, appointment details, etc.

<Mute>...</Mute>
    Speech inside this tag is suppressed entirely (not sent to TTS).
    Use for internal chain-of-thought the LLM produces but shouldn't
    speak aloud.

The parser streams LLM token chunks and yields TaggedSpeech objects
with the appropriate flags set.  Plain text (no tags) passes through
unchanged with default flags (interruptible=True, muted=False).
"""

from __future__ import annotations

import re
from typing import Any, AsyncIterable, Iterable, Optional

from lxml import etree
from pydantic import BaseModel, Field


class TaggedSpeech(BaseModel):
    text: str
    interruptible: bool = Field(default=True)
    muted: bool = Field(default=False)


_WRAPPER_TAG = "_LK"
_EXPECTED_TAGS = ("Mute", "NoInterrupt")


def _get_etree(msg: str) -> etree.Element:
    return etree.fromstring(f"<{_WRAPPER_TAG}>{msg}</{_WRAPPER_TAG}>")


def _tag_to_flags(tag: str, params: Optional[dict] = None) -> dict[str, Any]:
    if tag == "NoInterrupt":
        return {"interruptible": False}
    if tag == "Mute":
        return {"muted": True}
    return {}


def _process_xml_element(
    element: etree.Element,
    parent_flags: dict[str, Any],
) -> Iterable[TaggedSpeech]:
    merged = {**parent_flags, **_tag_to_flags(element.tag, dict(element.attrib))}

    if element.text and element.text.strip():
        yield TaggedSpeech(text=element.text, **merged)

    for child in element:
        yield from _process_xml_element(child, merged)

    if element.tail and element.tail.strip():
        yield TaggedSpeech(text=element.tail, **parent_flags)


async def enrich_speech_with_tags(
    text: AsyncIterable[str],
) -> AsyncIterable[TaggedSpeech]:
    """Stream LLM chunks, buffering when XML tags are detected.

    Yields ``TaggedSpeech`` objects with ``interruptible`` / ``muted``
    flags derived from the surrounding tags.
    """
    xml_pattern = re.compile(r"<\/?([a-zA-Z][a-zA-Z0-9:]*)[^>]*>")
    buffer = ""

    async for chunk in text:
        buffer += chunk

        should_wait = any(tag in buffer for tag in _EXPECTED_TAGS)
        matches = xml_pattern.findall(buffer)

        if any(tag in _EXPECTED_TAGS for tag in matches):
            try:
                tree = _get_etree(buffer)
                for tagged in _process_xml_element(tree, {}):
                    yield tagged
                buffer = ""
            except etree.XMLSyntaxError:
                continue
        elif not should_wait:
            yield TaggedSpeech(text=buffer)
            buffer = ""

    if buffer:
        yield TaggedSpeech(text=buffer)
