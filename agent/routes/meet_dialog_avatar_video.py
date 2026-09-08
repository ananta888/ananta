"""Bounded owner command and separately authenticated silent-clip hydration."""

import hmac
import re
import time

from flask import current_app, jsonify, request

from agent.auth import check_user_auth, get_authenticated_source_control_principal
from agent.services.meet_contract import MeetError
from ananta_contracts.meet_avatar_video import (
    MAX_AVATAR_VIDEO_BYTES,
    validate_video_request,
    video_request_signature,
    video_response_signature,
)
from ananta_contracts.meet_dialog import parse


def register_routes(blueprint):
    def service():
        if current_app.config.get("ROLE") != "hub":
            raise MeetError("meet_hub_required", 403)
        result = current_app.extensions.get("meet_dialog_service")
        if result is None:
            raise MeetError("meet_dialog_disabled", 404)
        return result

    @blueprint.put("/projects/<project>/dialogs/<task_id>/avatar-video")
    @check_user_auth
    def select_video(project, task_id):
        runtime = service()
        if (
            request.args
            or request.headers.get("Transfer-Encoding")
            or request.content_length is None
            or not 0 < request.content_length <= 2048
        ):
            raise MeetError("meet_dialog_avatar_video_selection_invalid")
        try:
            payload = parse(request.get_data(cache=False))
        except ValueError:
            raise MeetError("meet_dialog_avatar_video_selection_invalid") from None
        return jsonify(
            runtime.select_avatar_video(get_authenticated_source_control_principal(), project, task_id, payload)
        )

    @blueprint.post("/internal/dialog/avatar-video")
    def hydrate_video():
        runtime = service()
        key = current_app.extensions.get("meet_media_worker_key")
        if (
            key is None
            or request.headers.get("Authorization")
            or request.args
            or request.headers.get("Transfer-Encoding")
            or request.content_length is None
            or not 0 < request.content_length <= 16384
        ):
            raise MeetError("meet_avatar_video_callback_invalid", 403)
        raw, signature = request.get_data(cache=False), request.headers.get("X-Ananta-Avatar-Video-Signature", "")
        if not re.fullmatch(r"[a-f0-9]{64}", signature) or not hmac.compare_digest(
            video_request_signature(key, raw), signature
        ):
            raise MeetError("meet_avatar_video_callback_unauthorized", 401)
        try:
            payload = validate_video_request(parse(raw), time.time())
        except ValueError:
            raise MeetError("meet_avatar_video_callback_invalid") from None
        response = jsonify(runtime.avatar_video(payload))
        if len(response.get_data()) > MAX_AVATAR_VIDEO_BYTES:
            raise MeetError("meet_avatar_video_result_oversize", 502)
        response.headers["X-Ananta-Avatar-Video-Signature"] = video_response_signature(key, raw, response.get_data())
        return response
