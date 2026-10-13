"""Standalone isolated-mode entrypoint; restrict before running any tool code."""

import json
import os
import sys
from pathlib import Path


def main():
    try:
        if len(sys.argv) < 3:
            raise ValueError("native_tool_launch_invalid")
        policy = json.loads(sys.argv[1])
        if (set(policy) != {"readonly", "workspace", "workspace_fd"} or not isinstance(policy["readonly"], list)
                or not 1 <= len(policy["readonly"]) <= 32 or type(policy["workspace_fd"]) is not int
                or policy["workspace_fd"] < 3):
            raise ValueError("native_tool_launch_invalid")
        # Only the trusted sibling module is loaded before confinement; no
        # Worker/Hub application import, preexec_fn or thread-local restriction.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from landlock_kernel import LandlockKernel
        from metadata_seccomp import install_metadata_guard

        LandlockKernel().restrict(readonly=policy["readonly"], workspace=policy["workspace"],
                                 workspace_fd=policy["workspace_fd"])
        os.close(policy["workspace_fd"])
        install_metadata_guard()
        os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
    except Exception:
        print("native_tool_filesystem_isolation_denied", file=sys.stderr)
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
