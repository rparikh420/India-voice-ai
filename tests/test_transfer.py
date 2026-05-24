"""Tests for SIP call transfer helpers and tools."""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sip_helpers import resolve_transfer_target
from tools import transfer_call_cold, transfer_call_warm


# ---------------------------------------------------------------------------
# sip_helpers.resolve_transfer_target
# ---------------------------------------------------------------------------


def test_resolve_transfer_target_emergency():
    with patch("sip_helpers._FIXED_TARGETS", {"emergency": "+919876543210", "front_desk": "+919876543210"}):
        assert resolve_transfer_target("emergency") == "+919876543210"


def test_resolve_transfer_target_doctor():
    with patch("sip_helpers.TRANSFER_DOCTOR_NUMBERS", {"dr_patel": "+919876543211"}):
        assert resolve_transfer_target("dr_patel") == "+919876543211"


def test_resolve_transfer_target_raw_number():
    assert resolve_transfer_target("+919876543210") == "+919876543210"


def test_resolve_transfer_target_unknown():
    assert resolve_transfer_target("unknown_person") is None


def test_resolve_transfer_target_invalid_number():
    assert resolve_transfer_target("12345") is None


# ---------------------------------------------------------------------------
# sip_helpers.transfer_cold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transfer_cold_success():
    mock_api = MagicMock()
    mock_api.sip.transfer_sip_participant = AsyncMock(return_value=None)
    mock_api.aclose = AsyncMock()

    with patch("sip_helpers.get_livekit_api", return_value=mock_api):
        from sip_helpers import transfer_cold

        result = await transfer_cold("call-123", "sip-caller-1", "+919876543210")

    assert result["status"] == "transferred"
    assert result["transfer_to"] == "+919876543210"
    mock_api.sip.transfer_sip_participant.assert_awaited_once()
    mock_api.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_transfer_cold_api_error():
    mock_api = MagicMock()
    mock_api.sip.transfer_sip_participant = AsyncMock(side_effect=Exception("SIP error"))
    mock_api.aclose = AsyncMock()

    with patch("sip_helpers.get_livekit_api", return_value=mock_api):
        from sip_helpers import transfer_cold

        result = await transfer_cold("call-123", "sip-caller-1", "+919876543210")

    assert result["status"] == "error"
    assert "SIP error" in result["error"]


# ---------------------------------------------------------------------------
# sip_helpers.transfer_warm_dial
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transfer_warm_dial_success():
    mock_result = MagicMock()
    mock_result.sip_call_id = "sip-call-456"

    mock_api = MagicMock()
    mock_api.sip.create_sip_participant = AsyncMock(return_value=mock_result)
    mock_api.aclose = AsyncMock()

    with patch("sip_helpers.get_livekit_api", return_value=mock_api):
        from sip_helpers import transfer_warm_dial

        result = await transfer_warm_dial(
            "trunk-1", "+919876543211", "call-123", "sip-doctor-1", "dr_patel"
        )

    assert result["status"] == "connected"
    assert result["sip_call_id"] == "sip-call-456"


@pytest.mark.asyncio
async def test_transfer_warm_dial_error():
    mock_api = MagicMock()
    mock_api.sip.create_sip_participant = AsyncMock(side_effect=Exception("No answer"))
    mock_api.aclose = AsyncMock()

    with patch("sip_helpers.get_livekit_api", return_value=mock_api):
        from sip_helpers import transfer_warm_dial

        result = await transfer_warm_dial(
            "trunk-1", "+919876543211", "call-123", "sip-doctor-1", "dr_patel"
        )

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# tools.transfer_call_cold (call ._func directly to bypass FunctionTool wrapper)
# ---------------------------------------------------------------------------

_cold_fn = transfer_call_cold._func
_warm_fn = transfer_call_warm._func


@pytest.mark.asyncio
async def test_transfer_call_cold_unknown_target():
    ctx = MagicMock()
    ctx.disallow_interruptions = MagicMock()

    with patch("tools.resolve_transfer_target", return_value=None):
        out = await _cold_fn(ctx, "nobody")

    assert "error" in out
    assert "Unknown" in out["error"]


@pytest.mark.asyncio
async def test_transfer_call_cold_no_sip_participant():
    ctx = MagicMock()
    ctx.disallow_interruptions = MagicMock()

    with patch("tools.resolve_transfer_target", return_value="+919876543210"):
        with patch("tools._find_sip_participant", return_value=None):
            out = await _cold_fn(ctx, "emergency")

    assert "error" in out
    assert "phone calls" in out["error"]


@pytest.mark.asyncio
async def test_transfer_call_cold_success():
    mock_participant = MagicMock()
    mock_participant.identity = "sip-caller-1"

    ctx = MagicMock()
    ctx.disallow_interruptions = MagicMock()
    ctx.session.room.name = "call-123"

    with patch("tools.resolve_transfer_target", return_value="+919876543210"):
        with patch("tools._find_sip_participant", return_value=mock_participant):
            with patch("tools.transfer_cold", new_callable=AsyncMock) as mock_transfer:
                mock_transfer.return_value = {"status": "transferred", "transfer_to": "+919876543210"}
                out = await _cold_fn(ctx, "emergency")

    assert out["status"] == "transferred"
    mock_transfer.assert_awaited_once_with("call-123", "sip-caller-1", "+919876543210")


# ---------------------------------------------------------------------------
# tools.transfer_call_warm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transfer_call_warm_unknown_target():
    ctx = MagicMock()
    ctx.disallow_interruptions = MagicMock()

    with patch("tools.resolve_transfer_target", return_value=None):
        out = await _warm_fn(ctx, "nobody", "some briefing")

    assert "error" in out


@pytest.mark.asyncio
async def test_transfer_call_warm_no_outbound_trunk():
    ctx = MagicMock()
    ctx.disallow_interruptions = MagicMock()

    with patch("tools.resolve_transfer_target", return_value="+919876543211"):
        with patch("tools.SIP_OUTBOUND_TRUNK_ID", ""):
            out = await _warm_fn(ctx, "dr_patel", "tooth pain")

    assert "error" in out
    assert "trunk" in out["error"].lower()


@pytest.mark.asyncio
async def test_transfer_call_warm_success():
    mock_participant = MagicMock()
    mock_participant.identity = "sip-caller-1"

    ctx = MagicMock()
    ctx.disallow_interruptions = MagicMock()
    ctx.session.room.name = "call-123"

    with patch("tools.resolve_transfer_target", return_value="+919876543211"):
        with patch("tools.SIP_OUTBOUND_TRUNK_ID", "trunk-1"):
            with patch("tools._find_sip_participant", return_value=mock_participant):
                with patch("tools._get_sip_audio_track_sid", return_value="track-audio-1"):
                    with patch("tools.mute_sip_participant", new_callable=AsyncMock):
                        with patch("tools.transfer_warm_dial", new_callable=AsyncMock) as mock_dial:
                            mock_dial.return_value = {
                                "status": "connected",
                                "call_to": "+919876543211",
                                "participant_identity": "sip-doctor-1",
                                "sip_call_id": "call-456",
                            }
                            out = await _warm_fn(ctx, "dr_patel", "Patient has tooth pain")

    assert out["status"] == "warm_transfer_connected"
    assert "briefing" in out
    assert out["briefing"] == "Patient has tooth pain"


# ---------------------------------------------------------------------------
# ALL_TOOLS count
# ---------------------------------------------------------------------------


def test_all_tools_default_count():
    """With SIP_ENABLED=false (default), ALL_TOOLS should have 5 items."""
    from tools import ALL_TOOLS

    assert len(ALL_TOOLS) == 5
