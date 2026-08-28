from pathlib import Path
from threading import Event

from agent.cli_backends.coding_agent_process import BoundedCodingAgentProcess


def test_process_adapter_streams_and_redacts_secrets(tmp_path: Path) -> None:
    events = []
    result = BoundedCodingAgentProcess().run(
        (
            "/bin/sh",
            "-c",
            'printf "out:%s\\n" "$TOKEN"; printf "err:%s\\n" "$TOKEN" >&2',
        ),
        cwd=tmp_path,
        environment={"PATH": "/usr/bin:/bin", "TOKEN": "secret-value"},
        timeout_seconds=5,
        cancellation=Event(),
        maximum_output_chars=4096,
        event_sink=events.append,
        secret_values=("secret-value",),
    )

    assert result.return_code == 0
    assert "secret-value" not in result.stdout + result.stderr
    assert "<redacted>" in result.stdout
    assert {event.stream for event in events} == {"stdout", "stderr"}
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))


def test_process_adapter_times_out_without_waiting_for_input(tmp_path: Path) -> None:
    result = BoundedCodingAgentProcess().run(
        ("/bin/sh", "-c", "sleep 20"),
        cwd=tmp_path,
        environment={"PATH": "/usr/bin:/bin"},
        timeout_seconds=0.2,
        cancellation=Event(),
        maximum_output_chars=4096,
    )

    assert result.return_code == 124
    assert result.reason_code == "timeout"
    assert result.duration_ms < 5000


def test_process_adapter_enforces_output_bound(tmp_path: Path) -> None:
    result = BoundedCodingAgentProcess().run(
        ("/bin/sh", "-c", "yes 1234567890 | head -n 10000"),
        cwd=tmp_path,
        environment={"PATH": "/usr/bin:/bin"},
        timeout_seconds=5,
        cancellation=Event(),
        maximum_output_chars=1024,
    )

    assert result.return_code == 65
    assert result.reason_code == "output_limit_exceeded"
    assert result.output_truncated is True
    assert len(result.stdout) <= 1024


def test_process_adapter_honors_preexisting_cancellation(tmp_path: Path) -> None:
    cancellation = Event()
    cancellation.set()
    result = BoundedCodingAgentProcess().run(
        ("/bin/sh", "-c", "sleep 20"),
        cwd=tmp_path,
        environment={"PATH": "/usr/bin:/bin"},
        timeout_seconds=5,
        cancellation=cancellation,
        maximum_output_chars=4096,
    )

    assert result.return_code == 130
    assert result.reason_code == "cancelled"


def test_process_output_is_untrusted_data_and_never_executed(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    result = BoundedCodingAgentProcess().run(
        ("/bin/sh", "-c", f"printf 'touch {marker}\\n'"),
        cwd=tmp_path,
        environment={"PATH": "/usr/bin:/bin"},
        timeout_seconds=5,
        cancellation=Event(),
        maximum_output_chars=4096,
    )

    assert result.return_code == 0
    assert f"touch {marker}" in result.stdout
    assert marker.exists() is False
