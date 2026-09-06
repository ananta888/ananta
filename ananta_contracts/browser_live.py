"""Closed live-view request budgets; a request never authorizes capture or sharing."""

from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True)
class BrowserLiveViewRequest:
    schema: str = "ananta.browser-live-request.v1"
    source_kind: str = "agent_browser"
    width: int = 640
    height: int = 360
    max_fps: int = 5
    max_seconds: int = 30
    max_frame_bytes: int = 262144
    max_pending_frames: int = 1
    capture_authenticated: bool = False
    sensitive_content_policy: str = "deny_unknown"

    def __post_init__(self):
        if (
            self.schema != "ananta.browser-live-request.v1"
            or self.source_kind != "agent_browser"
            or self.sensitive_content_policy != "deny_unknown"
            or type(self.capture_authenticated) is not bool
        ):
            raise ValueError("browser_live_request_invalid")
        for name, lower, upper in (
            ("width", 320, 1280),
            ("height", 180, 720),
            ("max_fps", 1, 10),
            ("max_seconds", 1, 30),
            ("max_frame_bytes", 65536, 1048576),
            ("max_pending_frames", 1, 1),
        ):
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError("browser_live_budget_invalid")
        if self.width % 2 or self.height % 2:
            raise ValueError("browser_live_dimensions_invalid")

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict) or set(payload) - {field.name for field in fields(cls)}:
            raise ValueError("browser_live_request_invalid")
        return cls(**payload)
