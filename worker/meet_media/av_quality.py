"""Bounded decoding gate for the local fixed-format avatar, not a live-room SLA."""

import json
import re
from decimal import Decimal
from pathlib import Path

from voice_runtime.preprocessing.audio_decode import BoundedSubprocessRunner

MAX_VIDEO_BYTES = 3_500_000
RATE = 22_050


def _timestamp(frame):
    value = frame.get("best_effort_timestamp_time")
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,3}(?:\.\d{1,6})?", value):
        raise ValueError("meet_av_timestamp_invalid")
    return int(Decimal(value) * 1_000_000)


def _clock(samples, rate):
    return (samples * 1_000_000 + rate // 2) // rate


def validate_decoded_media(probe, *, expected_samples):
    if type(expected_samples) is not int or not 0 < expected_samples <= 40 * RATE:
        raise ValueError("meet_av_budget_invalid")
    if not isinstance(probe, dict) or not isinstance(probe.get("streams"), list) or len(probe["streams"]) != 2:
        raise ValueError("meet_av_streams_invalid")
    streams = {item.get("codec_type"): item for item in probe["streams"] if isinstance(item, dict)}
    if set(streams) != {"audio", "video"}:
        raise ValueError("meet_av_streams_invalid")
    audio, video = streams["audio"], streams["video"]
    if (
        audio.get("codec_name") != "aac"
        or audio.get("channels") != 1
        or audio.get("sample_rate") != str(RATE)
        or video.get("codec_name") != "h264"
        or video.get("width") != 256
        or video.get("height") != 256
        or video.get("r_frame_rate") != "12/1"
        or audio.get("index") == video.get("index")
        or any(type(item.get("index")) is not int for item in (audio, video))
    ):
        raise ValueError("meet_av_format_invalid")
    frames = probe.get("frames")
    if not isinstance(frames, list) or not 2 <= len(frames) <= 2048:
        raise ValueError("meet_av_frames_invalid")
    audio_samples = audio_frames = video_frames = 0
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError("meet_av_frames_invalid")
        stamp = _timestamp(frame)
        if frame.get("media_type") == "audio" and frame.get("stream_index") == audio["index"]:
            count = frame.get("nb_samples")
            if type(count) is not int or not 0 < count <= 1024 or abs(stamp - _clock(audio_samples, RATE)) > 2:
                raise ValueError("meet_av_audio_clock_invalid")
            audio_samples += count
            audio_frames += 1
        elif frame.get("media_type") == "video" and frame.get("stream_index") == video["index"]:
            if frame.get("width") != 256 or frame.get("height") != 256 or abs(stamp - _clock(video_frames, 12)) > 2:
                raise ValueError("meet_av_video_clock_invalid")
            video_frames += 1
        else:
            raise ValueError("meet_av_frame_source_invalid")
    expected_frames = (expected_samples * 12 + RATE - 1) // RATE
    if video_frames != expected_frames or not 0 <= audio_samples - expected_samples < 1024 or not audio_frames:
        raise ValueError("meet_av_decoded_extent_invalid")
    skew = abs(_clock(video_frames, 12) - _clock(audio_samples, RATE))
    if skew > 150_000:
        raise ValueError("meet_av_end_skew_exceeded")
    return {
        "profile": "ananta.local-avatar-av.v1",
        "video_frames_decoded": video_frames,
        "audio_frames_decoded": audio_frames,
        "audio_samples_decoded": audio_samples,
        "audio_padding_samples": audio_samples - expected_samples,
        "end_skew_us": skew,
        "live_delivery_verified": False,
    }


def verify_encoded_media(content, *, expected_samples, require_current, runner=None):
    if type(content) is not bytes or not 16 <= len(content) <= MAX_VIDEO_BYTES or content[4:8] != b"ftyp":
        raise ValueError("meet_av_media_invalid")
    if type(expected_samples) is not int or not 0 < expected_samples <= 40 * RATE:
        raise ValueError("meet_av_budget_invalid")
    require_current()
    result = (runner if runner is not None else BoundedSubprocessRunner()).run(
        [
            "/usr/bin/ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "pipe",
            "-f",
            "mov",
            "-i",
            "pipe:0",
            "-show_frames",
            "-show_entries",
            "stream=index,codec_name,codec_type,sample_rate,channels,width,height,r_frame_rate:"
            "frame=media_type,stream_index,best_effort_timestamp_time,nb_samples,width,height",
            "-of",
            "json",
        ],
        input_payload=content,
        max_stdout_bytes=2 * 1024 * 1024,
        timeout_seconds=5,
        cwd=Path(__file__).resolve().parent,
        cancellation_check=require_current,
    )
    require_current()
    if result.returncode != 0:
        raise ValueError("meet_av_decode_failed")
    try:
        report = validate_decoded_media(json.loads(result.stdout), expected_samples=expected_samples)
    except (TypeError, KeyError, ValueError):
        raise ValueError("meet_av_quality_failed") from None
    require_current()
    return report
