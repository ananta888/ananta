"""Ananta realtime/ — WebRTC DataChannel stack for Carbonyl browser mode.

This package is SEPARATE from webrtc_transport.py (Hub Relay).
See docs/operator-tui/carbonyl-webrtc-session.md for the architecture decision.

Modules:
    signaling_models      — SignalingMessage dataclass
    signaling_client      — WebSocket-based SignalingClient with URL allowlist
    datachannel_protocol  — DataChannelMessage dataclass + DataChannelProtocol v1
    webrtc_session_controller — Session orchestration (signaling + ICE + DC)
    webrtc_policy         — WebRtcPolicy dataclass
    ice_probe             — IceProbe / IceProbeResult
    webrtc_audit          — WebRtcAuditLog / WebRtcAuditEvent
"""
from __future__ import annotations
