"""Closed acceptance profiles, distinct from evidence issuance and execution."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MeetTestProfile:
    name: str
    node: str
    reference: str
    timeout_seconds: int
    settings: tuple[tuple[str, str], ...]

    def environment(self):
        # Ambient opt-ins must not turn a short reference into a different test.
        return {
            "ANANTA_TEST_DATABASE_MODE": "wal",
            "ANANTA_SQLITE_POOL_SIZE": "8",
            "ANANTA_MEET_MEDIA_TIMING": "1",
            "MEET_DIALOG_SOAK_SECONDS": "0",
            **dict(self.settings),
        }

    def projection(self):
        return {
            "schema": "ananta.meet-test-reference-profile.v1",
            "name": self.name,
            "node": self.node,
            "environment": self.environment(),
            "timeout_seconds": self.timeout_seconds,
            "reference": self.reference,
        }


DEFAULT_PROFILE = "packaged-resources"
_PROFILES = (
    MeetTestProfile(
        DEFAULT_PROFILE,
        "tests/test_meet_multi_worker_containers.py::"
        "test_two_role_assigned_packaged_workers_share_owned_screens_and_stop_independently[room-reconnect-media]",
        "two-cpu-one-gib-per-publisher-independent-media-v1",
        420,
        (("MEET_MULTI_WORKER_GATE", "1"), ("MEET_WORKER_RESOURCES_GATE", "1")),
    ),
    *(
        MeetTestProfile(
            name,
            "tests/test_meet_dialog_cross_repository.py::"
            f"test_actual_hub_worker_loop_receives_chat_shares_owned_cdp_and_obeys_stop[{case}]",
            "single-host-browser-packaged-qwen-piper-owned-canvas-v1",
            360,
            (("MEET_CROSS_REPOSITORY_GATE", "1"), ("MEET_DIALOG_GPU_GATE", "1")),
        )
        for name, case in (("gpu-avatar", "avatar-gpu"), ("gpu-voices", "voice-selection-gpu"))
    ),
    MeetTestProfile(
        "gpu-components",
        "tests/test_meet_dialog_gpu_turn.py::test_current_private_worker_runs_actual_qwen_piper_and_nvenc",
        "packaged-qwen-piper-nvenc-component-v1",
        240,
        (("MEET_DIALOG_GPU_GATE", "1"),),
    ),
)
PROFILE_NAMES = tuple(profile.name for profile in _PROFILES)


def select_profile(name):
    for profile in _PROFILES:
        if profile.name == name:
            return profile
    raise ValueError("meet_test_gate_profile_invalid")
