from __future__ import annotations

import subprocess
import sys


def run_smoke_once() -> tuple[bool, str]:
    command = [
        sys.executable,
        "-m",
        "client_surfaces.tui_runtime.ananta_tui",
        "--fixture",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    output = f"{result.stdout}\n{result.stderr}".strip()
    required_markers = ("[HEALTH]", "[TASKS]", "[ARTIFACTS]", "[APPROVALS]", "[REPAIRS]")
    markers_ok = all(marker in output for marker in required_markers)
    ok = result.returncode == 0 and markers_ok
    return ok, output


def main() -> int:
    ok, output = run_smoke_once()
    if ok:
        print("tui-runtime-smoke-ok")
        return 0
    print("tui-runtime-smoke-failed")
    print(output)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
