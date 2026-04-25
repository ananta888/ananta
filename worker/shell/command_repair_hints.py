from __future__ import annotations


def build_command_repair_hints(*, command: str, exit_code: int, stderr: str) -> list[str]:
    normalized_command = str(command or "").strip()
    normalized_stderr = str(stderr or "").lower()
    hints: list[str] = []
    if "not found" in normalized_stderr or exit_code == 127:
        hints.append("Command was not found. Verify installation and PATH before retrying.")
    if "no module named" in normalized_stderr:
        hints.append("Missing Python module detected. Install required dependencies in the active environment.")
    if "permission denied" in normalized_stderr:
        hints.append("Permission denied. Re-run via approved command path with correct filesystem permissions.")
    if "pytest" in normalized_command and exit_code != 0:
        hints.append("Test command failed. Start with the first failing test and run it in isolation.")
    if not hints:
        hints.append("Review stderr output and adjust command arguments or working directory.")
    return hints
