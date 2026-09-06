#!/usr/bin/env python3
"""Generate a local-only Compose overlay when NVIDIA Container Toolkit is absent.

No Docker daemon edits, privileged containers or host service restarts. Like
the existing Unsloth GPU gate, expose only GPU 0 and read-only driver libraries.
Regenerate after a host driver upgrade. Output belongs in private runtime data.
"""

import argparse
import json
import subprocess
from pathlib import Path


def generate():
    listing = subprocess.run(["ldconfig", "-p"], check=True, capture_output=True, text=True).stdout
    names = {
        "libcuda.so.1",
        "libnvidia-ml.so.1",
        "libnvidia-encode.so.1",
        "libnvcuvid.so.1",
        "libnvidia-ptxjitcompiler.so.1",
    }
    libraries = {}
    for line in listing.splitlines():
        if "=>" not in line or "x86-64" not in line:
            continue
        name = line.split()[0]
        if name in names or name.startswith(("libnvidia-gpucomp.so.", "libnvidia-nvvm.so.")):
            libraries[name] = str(Path(line.rsplit("=>", 1)[1].strip()).resolve(strict=True))
    if not names <= libraries.keys():
        raise ValueError("required_nvidia_driver_libraries_missing")
    devices = ["/dev/nvidia0", "/dev/nvidiactl", "/dev/nvidia-uvm", "/dev/nvidia-uvm-tools"]
    if any(not Path(device).is_char_device() for device in devices):
        raise ValueError("nvidia_devices_missing")
    volumes = [f"{path}:/host-nvidia/{name}:ro" for name, path in sorted(libraries.items())]
    volumes += [f"{libraries['libcuda.so.1']}:/host-nvidia/libcuda.so:ro", "/usr/bin/nvidia-smi:/usr/bin/nvidia-smi:ro"]
    result = "# Generated host-driver binding; runtime only. Compose >= 2.24 required.\nservices:\n"
    for service in ("meet-ollama", "meet-media-worker"):
        result += f"  {service}:\n    gpus: !reset []\n    devices: {json.dumps(devices)}\n"
        result += f"    volumes: {json.dumps(volumes)}\n    environment:\n      LD_LIBRARY_PATH: /host-nvidia\n"
        result += "      NVIDIA_VISIBLE_DEVICES: '0'\n"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    with args.output.open("x") as output:
        output.write(generate())
    print("Local GPU overlay generated; Docker daemon unchanged.")
