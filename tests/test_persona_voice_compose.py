"""Source-only infrastructure contract; parsing never deploys or creates keys."""

from pathlib import Path

import yaml


def test_voice_descriptor_worker_has_private_bounded_cpu_only_template():
    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / "docker-compose.persona-voices.yml").read_text())
    worker = compose["services"]["persona-voice-worker"]
    assert set(compose["services"]) == {"persona-voice-worker"}
    assert worker["read_only"] is True and worker["init"] is True
    assert worker["cap_drop"] == ["ALL"] and worker["security_opt"] == ["no-new-privileges:true"]
    assert worker["mem_limit"] == "128m" and worker["cpus"] == 0.5 and worker["pids_limit"] == 32
    assert not set(worker) & {"ports", "devices", "privileged", "network_mode", "gpus"}
    assert compose["networks"] == {"persona-voices": {"internal": True}}
    assert len(worker["volumes"]) == 2 and worker["volumes"][0].endswith("persona-voice-key:ro")
    assert set(worker["environment"]) == {"PERSONA_VOICE_WORKER_KEY_FILE", "PERSONA_VOICE_HUB_LEASE_URL"}
    dockerfile = (root / worker["build"]["dockerfile"]).read_text()
    assert "@sha256:" in dockerfile and "RUN " not in dockerfile
    assert '"worker.meet_media.persona_voice_server"' in dockerfile
