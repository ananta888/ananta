from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager


def _tty_ioctl_size(fd: int) -> tuple[int, int]:
    import fcntl
    import struct
    import termios

    data = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\x00" * 8)
    rows, cols = struct.unpack("HHHH", data)[:2]
    return int(cols), int(rows)


@contextmanager
def _open_dev_tty() -> Iterator[object]:
    with open("/dev/tty", "rb", buffering=0) as tty:
        yield tty


def get_tty_size(fallback: tuple[int, int] = (120, 32)) -> tuple[int, int]:
    """Return terminal size as (columns, rows) with /dev/tty ioctl + stdlib fallback."""
    try:
        with _open_dev_tty() as tty:
            fd_getter = getattr(tty, "fileno", None)
            if callable(fd_getter):
                cols, rows = _tty_ioctl_size(fd_getter())
                if cols > 0 and rows > 0:
                    return cols, rows
    except OSError:
        pass
    except ValueError:
        pass

    size = shutil.get_terminal_size(fallback)
    return int(size.columns), int(size.lines)
