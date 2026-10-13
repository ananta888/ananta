"""Deny filesystem metadata/descriptor escape operations Landlock cannot scope."""

import ctypes
import platform

# Verified against Linux UAPI headers and the kernel syscall tables. New
# syscalls (>=452) require review rather than inheriting ambient permissions.
_ARCH = {
    "x86_64": (0xC000003E, {16, 90, 91, 92, 93, 94, 101, 132, 165, 166, 188, 189, 190,
                            197, 198, 199, 235, 260, 261, 268, 280, 308, 310, 311}),
    "aarch64": (0xC00000B7, {5, 6, 7, 14, 15, 16, 29, 39, 40, 52, 53, 54, 55, 88,
                             117, 268, 270, 271}),
}
_COMMON_DENIED = {425, 426, 427, 428, 429, 430, 431, 432, 433, 438, 440, 442}
_DENY = 0x00050001  # SECCOMP_RET_ERRNO | EPERM
_ALLOW = 0x7FFF0000


class Filter(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8), ("jf", ctypes.c_uint8),
                ("k", ctypes.c_uint32)]


class Program(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint16), ("instructions", ctypes.POINTER(Filter))]


def install_metadata_guard():
    entry = _ARCH.get(platform.machine())
    if entry is None:
        raise PermissionError("native_tool_seccomp_platform_unavailable")
    architecture, denied = entry
    ioctl = 16 if platform.machine() == "x86_64" else 29
    instructions = [Filter(0x20, 0, 0, 4), Filter(0x15, 1, 0, architecture),
                    Filter(0x06, 0, 0, 0x80000000),  # Kill foreign/compat architecture.
                    Filter(0x20, 0, 0, 0), Filter(0x35, 0, 1, 452), Filter(0x06, 0, 0, _DENY)]
    # Python needs close-on-exec flags; Node/libuv needs nonblocking stdio.
    # These requests change descriptor/open-description flags, not inode
    # metadata or device state. Every other ioctl remains denied.
    requests = (0x5451, 0x5450, 0x5421)  # FIOCLEX, FIONCLEX, FIONBIO.
    instructions.extend((Filter(0x15, 0, len(requests) + 4, ioctl), Filter(0x20, 0, 0, 24)))
    for index, request in enumerate(requests):
        instructions.append(Filter(0x15, len(requests) - index, 0, request))
    instructions.extend((Filter(0x06, 0, 0, _DENY), Filter(0x06, 0, 0, _ALLOW), Filter(0x20, 0, 0, 0)))
    for number in sorted(denied | _COMMON_DENIED):
        instructions.extend((Filter(0x15, 0, 1, number), Filter(0x06, 0, 0, _DENY)))
    instructions.append(Filter(0x06, 0, 0, _ALLOW))
    array = (Filter * len(instructions))(*instructions)
    program = Program(len(instructions), array)
    libc = ctypes.CDLL(None, use_errno=True)
    # Landlock installation already set no_new_privs in this launcher.
    if libc.prctl(22, 2, ctypes.byref(program), 0, 0) != 0:
        raise PermissionError("native_tool_seccomp_unavailable")
