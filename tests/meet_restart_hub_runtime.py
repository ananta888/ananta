"""Opt-in installed test Hub; bounded static configuration and no secret diagnostics."""

import ipaddress
import json
import logging
import os
import re
import traceback
from pathlib import Path
from urllib.parse import urlsplit


def validate_config(value):
    if not isinstance(value, dict) or set(value) != {"meeting_origin", "room_id", "worker_origin"}:
        raise ValueError("test_hub_config_invalid")
    if not isinstance(value["room_id"], str) or not re.fullmatch(r"room-[a-f0-9]{18}", value["room_id"]):
        raise ValueError("test_hub_config_invalid")
    for field, scheme in (("meeting_origin", "https"), ("worker_origin", "http")):
        if not isinstance(value[field], str) or any(character.isspace() for character in value[field]):
            raise ValueError("test_hub_config_invalid")
        url = urlsplit(value[field])
        if (
            url.scheme != scheme
            or not url.hostname
            or url.path
            or url.query
            or url.fragment
            or url.username
            or url.password
            or not ipaddress.IPv4Address(url.hostname).is_private
        ):
            raise ValueError("test_hub_config_invalid")
        if url.port not in ({None, 443} if field == "meeting_origin" else {8094}):
            raise ValueError("test_hub_config_invalid")
    return value


def main():
    if (
        os.environ.get("MEET_HUB_RESTART_GATE") != "1"
        or os.environ.get("DATABASE_URL") != "sqlite:////state/hub.sqlite"
        or os.environ.get("DATA_DIR") != "/state/data"
        or os.environ.get("ROLE") != "hub"
    ):
        raise ValueError("test_hub_opt_in_required")
    path = Path("/test/hub-config.json")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        raise ValueError("test_hub_config_invalid")
    config = validate_config(json.loads(path.read_bytes()))
    logging.disable(logging.CRITICAL)
    from werkzeug.serving import WSGIRequestHandler, make_server

    from tests.meet_restart_hub_app import create_restart_hub

    class Quiet(WSGIRequestHandler):
        def log(self, *_args, **_kwargs):
            pass

    app = create_restart_hub(config)
    with make_server("0.0.0.0", 8099, app, threaded=True, request_handler=Quiet) as server:
        server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        report = {"error": "test_hub_runtime_failed", "type": type(error).__name__}
        if os.environ.get("MEET_HUB_RESTART_GATE") == "1":
            report["frames"] = [
                {"file": Path(frame.filename).name, "line": frame.lineno}
                for frame in traceback.extract_tb(error.__traceback__)[-4:]
                if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}\.py", Path(frame.filename).name)
            ]
        print(json.dumps(report), flush=True)
        raise SystemExit(1) from None
