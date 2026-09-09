"""Current-source GPU probes in a separate container, never the serving worker.

Use only a local image, models, source packages and allowlisted driver mounts.
No worker key/state, environment, Docker socket, network or public port passes.
"""

import json
import re
import subprocess
from pathlib import Path
from uuid import uuid4

MODULES = frozenset(
    {
        "persona_visual_smoke",
        "speech_smoke",
        "persona_video_smoke",
        "speech_pcm_probe",
        "voice_variant_smoke",
        "asr_smoke",
    }
)
DRIVER = re.compile(r"lib(?:cuda|nvcuvid|nvidia-(?:encode|ml|nvvm|gpucomp|ptxjitcompiler))\.so(?:\.[0-9]+)*")
REQUIRED = frozenset({"libcuda.so.1", "libcuda.so", "libnvidia-encode.so.1", "libnvcuvid.so.1"})
ROOT = Path(__file__).resolve().parents[1]


def docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True, timeout=55).stdout.strip()


def driver_bindings(mounts):
    """Project an allowlist, not the service's private mounts or configuration."""
    result = {}
    if not isinstance(mounts, list):
        raise ValueError("test_gpu_mounts_invalid")
    for mount in mounts:
        if not isinstance(mount, dict):
            raise ValueError("test_gpu_mounts_invalid")
        destination = mount.get("Destination", "")
        if not isinstance(destination, str) or not destination.startswith("/host-nvidia/"):
            continue
        name = destination.removeprefix("/host-nvidia/")
        source = mount.get("Source", "")
        if (
            not DRIVER.fullmatch(name)
            or not isinstance(source, str)
            or not source.startswith("/usr/lib/x86_64-linux-gnu/")
            or not DRIVER.fullmatch(source.removeprefix("/usr/lib/x86_64-linux-gnu/"))
            or mount.get("Type") != "bind"
            or mount.get("RW") is not False
            or name in result
        ):
            raise ValueError("test_gpu_driver_binding_invalid")
        result[name] = source
    if not REQUIRED <= result.keys():
        raise ValueError("test_gpu_driver_binding_missing")
    return [f"type=bind,src={source},dst=/host-nvidia/{name},readonly" for name, source in sorted(result.items())]


def probe_command(name, image, bindings, module, root=ROOT, *, packaged=False, asr_profile=None):
    if module not in MODULES or not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("test_gpu_probe_invalid")
    if not re.fullmatch(r"meet-test-gpu-[a-f0-9-]{36}", name):
        raise ValueError("test_gpu_probe_name_invalid")
    if (
        type(packaged) is not bool
        or asr_profile is not None
        and (module != "asr_smoke" or asr_profile not in ("bounded-4s-vad", "bounded-4s-no-vad"))
    ):
        raise ValueError("test_gpu_probe_profile_invalid")
    args = [
        "create",
        "--name",
        name,
        "--pull=never",
        "--network=none",
        "--read-only",
        "--user=1000:1000",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--memory=4g",
        "--pids-limit=128",
        "--tmpfs=/tmp:rw,size=256m,mode=1777",
        "--entrypoint=timeout",
    ]
    for device in ("nvidia0", "nvidiactl", "nvidia-uvm", "nvidia-uvm-tools"):
        args += ["--device", f"/dev/{device}"]
    mounts = [(root / "data/meet-media/models", "/models")]
    if not packaged:
        mounts += [
            (root / "worker/meet_media", "/app/worker/meet_media"),
            (root / "ananta_contracts", "/app/ananta_contracts"),
        ]
    for source, destination in mounts:
        args += ["--mount", f"type=bind,src={source},dst={destination},readonly"]
    for binding in bindings:
        args += ["--mount", binding]
    if asr_profile is not None:
        args += ["--env", "MEET_ASR_SMOKE_PROFILE=" + asr_profile]
    return args + [
        "--env=LD_LIBRARY_PATH=/host-nvidia",
        image,
        "--signal=TERM",
        "--kill-after=5",
        "45",
        "python",
        "-m",
        f"worker.meet_media.{module}",
    ]


def run_probe(module, *, command=docker, packaged_image=None, asr_profile=None):
    if module not in MODULES:
        raise ValueError("test_gpu_probe_invalid")
    # Inspect only image identity and mounts, never service environment/secrets.
    service = "ananta-meet-media-meet-media-worker-1"
    if packaged_image is not None:
        if not isinstance(packaged_image, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", packaged_image):
            raise ValueError("test_gpu_probe_image_invalid")
        image = command("image", "inspect", packaged_image, "--format", "{{.Id}}")
        if image != packaged_image:
            raise ValueError("test_gpu_probe_image_mismatch")
    else:
        image = command("inspect", service, "--format", "{{.Image}}")
    bindings = driver_bindings(json.loads(command("inspect", service, "--format", "{{json .Mounts}}")))
    name = "meet-test-gpu-" + str(uuid4())
    args = probe_command(name, image, bindings, module, packaged=packaged_image is not None, asr_profile=asr_profile)
    try:
        command(*args)
        output = command("start", "--attach", name)
        report = json.loads(output.splitlines()[-1])
        if (
            not isinstance(report, dict)
            or report.get("status") != "passed"
            or report.get("production_release_evidence") is not False
            or report.get("human_capture_used") is not False
        ):
            raise ValueError("test_gpu_probe_failed")
        return report
    finally:
        # Exact test-owned UUID, also after uncertain create/start/timeout.
        command("rm", "--force", name)
