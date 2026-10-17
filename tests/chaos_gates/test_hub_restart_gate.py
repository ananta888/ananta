import subprocess
import sys
from pathlib import Path


def test_backpressure_soak_script_exists():
    script = Path(__file__).resolve().parents[2] / "scripts" / "gates" / "backpressure_soak.sh"
    assert script.exists(), f"Missing backpressure soak script: {script}"
    assert script.read_text().strip(), "Backpressure soak script is empty"


def test_backpressure_soak_script_executable():
    script = Path(__file__).resolve().parents[2] / "scripts" / "gates" / "backpressure_soak.sh"
    import os
    assert os.access(script, os.X_OK), f"Backpressure soak script not executable: {script}"


def test_hub_restart_gate_scope():
    # The gate must cover Hub-Restart, DB/Redis/Search/Relay/SFU outages,
    # slow receivers, network switches and queue pressure.
    # Buzz failures are explicitly excluded from this gate.
    expected_keywords = [
        "Hub-Restart",
        "DB/Redis/Search/Relay/SFU",
        "slow receivers",
        "network switch",
        "queue pressure",
        "Buzz-Gate",
    ]
    script_text = (Path(__file__).resolve().parents[2] / "scripts" / "gates" / "backpressure_soak.sh").read_text()
    for kw in expected_keywords:
        assert kw.lower() in script_text.lower(), f"Missing keyword in soak script: {kw}"


def test_buzz_exclusion():
    # Buzz failures must not be part of this gate.
    script_text = (Path(__file__).resolve().parents[2] / "scripts" / "gates" / "backpressure_soak.sh").read_text()
    assert "Buzz-Gate" in script_text, "Buzz exclusion comment missing"
