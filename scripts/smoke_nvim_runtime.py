from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NVIM_RUNTIME_PATH = ROOT / "client_surfaces" / "nvim_runtime"
REQUIRED_COMMANDS = (
    "AnantaGoalSubmit",
    "AnantaAnalyze",
    "AnantaReview",
    "AnantaPatchPlan",
    "AnantaProjectNew",
    "AnantaProjectEvolve",
)


def _build_check_commands() -> str:
    checks = []
    for command in REQUIRED_COMMANDS:
        checks.append(("if vim.fn.exists(':%s') ~= 2 then error('missing_command:%s') end" % (command, command)))
    checks.append("local res=require('ananta').analyze()")
    checks.append("if not res or res.ok ~= true then error('mock_command_failed') end")
    checks.append("print('nvim-runtime-smoke-ok')")
    return " ".join(checks)


def run_smoke_once() -> tuple[bool, str]:
    nvim_bin = shutil.which("nvim")
    if not nvim_bin:
        return True, "nvim-runtime-smoke-skipped:nvim-not-found"

    lua_check = _build_check_commands()
    command = [
        nvim_bin,
        "--headless",
        "-u",
        "NONE",
        "-c",
        f"set rtp^={NVIM_RUNTIME_PATH}",
        "-c",
        "lua require('ananta').setup({confirm_context=false, render_style='split'})",
        "-c",
        f"lua {lua_check}",
        "-c",
        "qa!",
    ]
    env = os.environ.copy()
    env["ANANTA_NVIM_FIXTURE"] = "1"
    result = subprocess.run(command, check=False, capture_output=True, text=True, env=env, cwd=str(ROOT))
    output = f"{result.stdout}\n{result.stderr}".strip()
    ok = result.returncode == 0 and "nvim-runtime-smoke-ok" in output
    return ok, output


def main() -> int:
    ok, output = run_smoke_once()
    if ok:
        print(output)
        return 0
    print("nvim-runtime-smoke-failed")
    print(output)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
