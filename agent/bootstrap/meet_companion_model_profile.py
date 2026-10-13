"""Operator-owned closed model policy configuration; never Worker input."""

import os

from ananta_contracts.meet_companion_task_grant import validate_model_execution
from ananta_contracts.meet_companion_task_tool import parse

CONFIG = "MEET_COMPANION_ASSIGNED_MODEL_PROFILE"
ENVIRONMENT = "ANANTA_MEET_COMPANION_MODEL_PROFILE"


def configure_model_profile(app):
    value = app.config.get(CONFIG)
    if CONFIG not in app.config:
        raw = os.environ.get(ENVIRONMENT, "")
        if not raw:
            return
        try:
            value = parse(raw.encode("utf-8"), 2048)
        except (ValueError, UnicodeError):
            raise ValueError("meet_companion_model_profile_config_invalid") from None
    try:
        if (not isinstance(value, dict)
                or set(value) != {"backend", "model", "environment_digest", "auto_execute"}
                or type(value["auto_execute"]) is not bool):
            raise ValueError()
        profile = validate_model_execution({key: value[key] for key in ("backend", "model", "environment_digest")})
    except (ValueError, TypeError, KeyError):
        raise ValueError("meet_companion_model_profile_config_invalid") from None
    app.config[CONFIG] = profile | {"auto_execute": value["auto_execute"]}
