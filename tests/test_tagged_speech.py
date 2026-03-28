"""Tests for tagged_speech module."""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from tagged_speech import TaggedSpeech, enrich_speech_with_tags


async def _to_async_iter(chunks: list[str]):
    """Create an async iterable from a list of strings."""
    for c in chunks:
        yield c


async def _collect(ait):
    """Collect all items from an async iterable into a list."""
    return [item async for item in ait]


@pytest.mark.asyncio
async def test_plain_text_no_tags():
    """Plain text with no tags yields TaggedSpeech with default flags."""
    chunks = ["Hello, how can I help you?"]
    result = await _collect(enrich_speech_with_tags(_to_async_iter(chunks)))
    assert len(result) == 1
    assert result[0].text == "Hello, how can I help you?"
    assert result[0].interruptible is True
    assert result[0].muted is False


@pytest.mark.asyncio
async def test_no_interrupt_tag():
    """Text wrapped in <NoInterrupt> yields TaggedSpeech with interruptible=False."""
    chunks = ["<NoInterrupt>Appointment confirmed for tomorrow at 10am.</NoInterrupt>"]
    result = await _collect(enrich_speech_with_tags(_to_async_iter(chunks)))
    assert len(result) == 1
    assert result[0].text == "Appointment confirmed for tomorrow at 10am."
    assert result[0].interruptible is False
    assert result[0].muted is False


@pytest.mark.asyncio
async def test_mute_tag():
    """Text wrapped in <Mute> yields TaggedSpeech with muted=True."""
    chunks = ["<Mute>Let me think about that...</Mute>"]
    result = await _collect(enrich_speech_with_tags(_to_async_iter(chunks)))
    assert len(result) == 1
    assert result[0].text == "Let me think about that..."
    assert result[0].interruptible is True
    assert result[0].muted is True


@pytest.mark.asyncio
async def test_mixed_content():
    """Mixed content yields multiple TaggedSpeech objects with correct flags."""
    text = "Hello <NoInterrupt>Appointment confirmed</NoInterrupt> anything else?"
    chunks = [text]
    result = await _collect(enrich_speech_with_tags(_to_async_iter(chunks)))
    assert len(result) == 3
    assert result[0].text == "Hello "
    assert result[0].interruptible is True
    assert result[0].muted is False
    assert result[1].text == "Appointment confirmed"
    assert result[1].interruptible is False
    assert result[1].muted is False
    assert result[2].text == " anything else?"
    assert result[2].interruptible is True
    assert result[2].muted is False


@pytest.mark.asyncio
async def test_nested_tags():
    """Nested tags work correctly - inner inherits outer flags."""
    chunks = ["<NoInterrupt>Outer <Mute>inner thought</Mute> more outer</NoInterrupt>"]
    result = await _collect(enrich_speech_with_tags(_to_async_iter(chunks)))
    assert len(result) == 3
    assert result[0].text == "Outer "
    assert result[0].interruptible is False
    assert result[0].muted is False
    assert result[1].text == "inner thought"
    assert result[1].interruptible is False
    assert result[1].muted is True
    assert result[2].text == " more outer"
    assert result[2].interruptible is False
    assert result[2].muted is False


@pytest.mark.asyncio
async def test_empty_input_yields_nothing():
    """Empty input yields nothing."""
    chunks: list[str] = []
    result = await _collect(enrich_speech_with_tags(_to_async_iter(chunks)))
    assert len(result) == 0


@pytest.mark.asyncio
async def test_partial_xml_tags_buffer_correctly():
    """Partial XML tags buffer correctly when streaming in chunks."""
    # Simulate streaming: "<NoInterrupt>" and "Appointment</NoInterrupt>" in separate chunks
    chunks = ["<NoInterrupt>", "Appointment ", "confirmed", "</NoInterrupt>"]
    result = await _collect(enrich_speech_with_tags(_to_async_iter(chunks)))
    assert len(result) == 1
    assert result[0].text == "Appointment confirmed"
    assert result[0].interruptible is False
    assert result[0].muted is False
