"""Small Linux Landlock ABI adapter; called only in a single-threaded launcher."""

import ctypes
import os
import platform
import stat
import sys

READ = (1 << 0) | (1 << 2) | (1 << 3)
HANDLED_FS = (1 << 16) - 1  # ABI 5 filesystem rights, including truncate/ioctl.
WORKSPACE = HANDLED_FS & ~((1 << 6) | (1 << 11) | (1 << 15))


class Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64),
                ("scoped", ctypes.c_uint64)]


class PathRule(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


class LandlockKernel:
    def __init__(self):
        if sys.platform != "linux" or platform.machine() not in {"x86_64", "aarch64"}:
            raise PermissionError("native_tool_landlock_platform_unavailable")
        self._libc = ctypes.CDLL(None, use_errno=True)
        self._libc.syscall.restype = ctypes.c_long

    def _call(self, number, *args):
        result = self._libc.syscall(ctypes.c_long(number), *args)
        if result < 0:
            raise PermissionError("native_tool_landlock_unavailable")
        return result

    def _path_rule(self, ruleset, path, rights, *, pinned_descriptor=None):
        descriptor = pinned_descriptor if pinned_descriptor is not None else os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                rights &= (1 << 0) | (1 << 1) | (1 << 2) | (1 << 14) | (1 << 15)
            if path == "/dev/null":
                rights |= 1 << 1
            rule = PathRule(rights, descriptor)
            self._call(445, ruleset, 1, ctypes.byref(rule), 0)
        finally:
            if pinned_descriptor is None:
                os.close(descriptor)

    def restrict(self, *, readonly, workspace, workspace_fd=None):
        if self._call(444, 0, 0, 1) < 6:
            raise PermissionError("native_tool_landlock_abi_required")
        if not self._call(444, 0, 0, 2) & 4:  # Require disconnected-directory erratum 3 fix.
            raise PermissionError("native_tool_landlock_erratum_required")
        # ABI 6 also scopes signals and abstract Unix sockets to this domain.
        attributes = Ruleset(HANDLED_FS, 0, 3)
        ruleset = self._call(444, ctypes.byref(attributes), ctypes.sizeof(attributes), 0)
        try:
            for path in readonly:
                self._path_rule(ruleset, path, READ)
            self._path_rule(ruleset, workspace, WORKSPACE, pinned_descriptor=workspace_fd)
            if self._libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
                raise PermissionError("native_tool_privilege_restriction_unavailable")
            self._call(446, ruleset, 0)
        finally:
            os.close(ruleset)
