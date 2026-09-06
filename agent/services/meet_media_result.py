"""Transport-neutral validation of generated Meet media and response budgets."""

import base64
import math

from agent.services.meet_contract import MeetError
from agent.services.meet_speech_result import validate_speech_binding, validate_speech_receipt


def validate_result(result):
    fields = {"schema", "task_id", "lease_id", "text", "audio", "video", "duration_seconds", "engines"}
    if (
        not isinstance(result, dict)
        or set(result) - {"meeting", "usage", "persona_image", "speech"} != fields
        or result["schema"] != "ananta.meet-turn-result.v1"
    ):
        raise MeetError("meet_worker_result_invalid", 502)
    if not isinstance(result["text"], str) or not result["text"].strip() or not 1 <= len(result["text"]) <= 450:
        raise MeetError("meet_worker_result_invalid", 502)
    if "usage" in result:
        usage = result["usage"]
        if (
            not isinstance(usage, dict)
            or set(usage) != {"input_tokens", "output_tokens"}
            or any(
                type(usage[field]) is not int or not 1 <= usage[field] <= maximum
                for field, maximum in (("input_tokens", 2048), ("output_tokens", 128))
            )
        ):
            raise MeetError("meet_worker_usage_invalid", 502)
    duration = result["duration_seconds"]
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 40:
        raise MeetError("meet_worker_result_invalid", 502)
    for field, mime, maximum in (("audio", "audio/wav", 2_000_000), ("video", "video/mp4", 3_500_000)):
        item = result[field]
        if not isinstance(item, dict) or set(item) != {"mime", "base64"} or item["mime"] != mime:
            raise MeetError("meet_worker_media_invalid", 502)
        try:
            decoded = base64.b64decode(item["base64"], validate=True)
        except (ValueError, TypeError):
            raise MeetError("meet_worker_media_invalid", 502) from None
        valid_header = (
            decoded[:4] == b"RIFF" and decoded[8:12] == b"WAVE" if field == "audio" else decoded[4:8] == b"ftyp"
        )
        if not valid_header or not 16 <= len(decoded) <= maximum:
            raise MeetError("meet_worker_media_invalid", 502)
    if result["engines"] != {
        "llm": "ollama",
        "speech": "piper-cuda",
        "video": "persona-image-h264_nvenc" if "persona_image" in result else "procedural-avatar-h264_nvenc",
    }:
        raise MeetError("meet_worker_engines_invalid", 502)
    if "persona_image" in result:
        from ananta_contracts.meet_persona_image import validate_reference

        try:
            validate_reference(result["persona_image"])
        except ValueError:
            raise MeetError("meet_worker_persona_invalid", 502) from None
    if "meeting" in result:
        import re

        meeting = result["meeting"]
        if (
            not isinstance(meeting, dict)
            or set(meeting) != {"status", "room_id", "delivery_verified"}
            or meeting["status"] != "published"
            or meeting["delivery_verified"] is not False
            or not isinstance(meeting["room_id"], str)
            or not re.fullmatch(r"room-[a-f0-9]{18}", meeting["room_id"])
        ):
            raise MeetError("meet_worker_publication_invalid", 502)
    validate_speech_receipt(result)
    return result


def validate_response_budget(turn, result):
    validate_speech_binding(turn, result)
    if ("persona_image" in turn) != ("persona_image" in result) or (
        "persona_image" in turn and turn["persona_image"]["reference"] != result["persona_image"]
    ):
        raise MeetError("meet_worker_persona_mismatch", 502)
    if ("response_limits" in turn) != ("usage" in result):
        raise MeetError("meet_worker_usage_mismatch", 502)
    if "response_limits" in turn and (
        len(result["text"]) > turn["response_limits"]["max_reply_chars"]
        or result["usage"]["output_tokens"] > turn["response_limits"]["max_output_tokens"]
    ):
        raise MeetError("meet_worker_budget_exceeded", 502)
