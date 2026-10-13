"""Worker-owned filesystem policy for commands sharing a private result-store UID."""

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path


def _overlaps(first, second):
    return first == second or first in second.parents or second in first.parents


def configured_private_result_path():
    from flask import current_app, has_app_context

    if has_app_context():
        if "native_result_store" in current_app.extensions:
            path = getattr(current_app.extensions["native_result_store"], "path", None)
            if not path:
                raise PermissionError("native_tool_private_store_binding_unavailable")
            # The active store remains authoritative after configuration drift;
            # clearing its config must not enable an unrestricted command.
            return path
        configured = current_app.config.get("ANANTA_NATIVE_RESULT_STORE_PATH")
        if configured:
            return configured
    return os.environ.get("ANANTA_NATIVE_RESULT_STORE_PATH")


@contextmanager
def isolated_command(argv, *, workspace, environment, readonly_paths=(), allowed_environment_keys=()):
    """Compose a trusted launcher; task-provided environment cannot disable it."""
    configured = configured_private_result_path()
    if not configured:
        yield argv, environment, ()
        return
    if sys.platform != "linux" or os.geteuid() == 0:
        raise PermissionError("native_tool_landlock_platform_unavailable")
    private = Path(configured)
    root = Path(workspace).resolve(strict=True)
    implementation = Path(__file__).resolve().parent
    if (not private.is_absolute() or private.is_symlink()
            or private.parent.resolve(strict=True) != private.parent
            or _overlaps(root, private.parent) or root == implementation or root in implementation.parents):
        raise PermissionError("native_tool_filesystem_binding_denied")
    candidates = ["/usr", "/bin", "/lib", "/lib64", sys.prefix,
                  "/etc/ld.so.cache", "/etc/localtime", "/etc/passwd", "/etc/group",
                  "/etc/nsswitch.conf", "/etc/ssl/certs", "/etc/ssl/openssl.cnf",
                  "/dev/null", "/dev/urandom"]
    if len(readonly_paths) > 16 or len(allowed_environment_keys) > 16:
        raise PermissionError("native_tool_filesystem_profile_invalid")
    candidates.extend(str(Path(path).resolve(strict=True)) for path in readonly_paths)
    readonly = sorted({str(Path(path).resolve(strict=True)) for path in candidates if Path(path).exists()})
    if any(_overlaps(Path(path), private.parent) for path in readonly):
        raise PermissionError("native_tool_filesystem_binding_denied")
    # Python isolated mode ignores PYTHONPATH/site configuration. The launcher
    # receives no dynamic-loader, provider, service-token or result-store env.
    allowed = {"PATH", "LANG", "LC_ALL"} | set(allowed_environment_keys)
    if allowed & {"LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME", "NODE_OPTIONS",
                  "AGENT_TOKEN", "SECRET_KEY", "ANANTA_NATIVE_RESULT_STORE_PATH"}:
        raise PermissionError("native_tool_filesystem_profile_invalid")
    bounded = {key: value for key, value in environment.items() if key in allowed}
    bounded.update(HOME=str(root), TMPDIR=str(root))
    launcher = implementation / "filesystem_launcher.py"
    descriptor = os.open(root, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if os.readlink(f"/proc/self/fd/{descriptor}") != str(root):
            raise PermissionError("native_tool_filesystem_binding_denied")
        policy = json.dumps({"readonly": readonly, "workspace": str(root), "workspace_fd": descriptor},
                            separators=(",", ":"))
        yield [sys.executable, "-I", str(launcher), policy, *argv], bounded, (descriptor,)
    finally:
        os.close(descriptor)
