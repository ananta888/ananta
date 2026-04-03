from __future__ import annotations

import atexit
import logging
import threading
from queue import Empty, Full, Queue
from typing import List

from .process import PersistentShell
from .runtime import SHELL_POOL_BUSY, SHELL_POOL_FREE, SHELL_POOL_SIZE, settings


class ShellPool:
    def __init__(self, size: int = 5, shell_cmd: str = None):
        self.size = size
        self.shell_cmd = shell_cmd
        self.pool = Queue(maxsize=size)
        self.shells: List[PersistentShell] = []
        self.lock = threading.Lock()
        for _ in range(size):
            shell = PersistentShell(shell_cmd=shell_cmd)
            self.shells.append(shell)
            self.pool.put(shell)
        self._update_metrics()
        logging.info(f"ShellPool mit {size} Instanzen initialisiert.")

    def _update_metrics(self):
        try:
            free = self.pool.qsize()
            busy = len(self.shells) - free
            SHELL_POOL_SIZE.set(len(self.shells))
            SHELL_POOL_BUSY.set(busy)
            SHELL_POOL_FREE.set(free)
        except Exception as exc:
            logging.error(f"Fehler beim Update der ShellPool-Metriken: {exc}")

    def acquire(self, timeout: int = 10) -> PersistentShell:
        try:
            shell = self.pool.get(timeout=timeout)
            if not shell.is_healthy():
                logging.warning("Shell im Pool ist nicht gesund. Starte neu...")
                shell._start_process()
            self._update_metrics()
            return shell
        except Empty:
            logging.warning("Keine Shell im Pool verfuegbar. Erstelle temporaere Shell.")
            return PersistentShell(shell_cmd=self.shell_cmd)

    def release(self, shell: PersistentShell):
        if shell in self.shells:
            try:
                self.pool.put_nowait(shell)
            except Full:
                shell.close()
        else:
            shell.close()
        self._update_metrics()

    def close_all(self):
        with self.lock:
            for shell in self.shells:
                shell.close()
            self.shells.clear()
            while not self.pool.empty():
                try:
                    self.pool.get_nowait()
                except Empty:
                    break


_shell_instance = None
_shell_pool = None


def get_shell() -> PersistentShell:
    global _shell_instance
    if _shell_instance is None:
        _shell_instance = PersistentShell()
    return _shell_instance


def get_shell_pool(size: int = None) -> ShellPool:
    global _shell_pool
    if _shell_pool is None:
        if size is None:
            size = settings.shell_pool_size
        _shell_pool = ShellPool(size=size)
    return _shell_pool


def _close_global_shells():
    global _shell_instance, _shell_pool
    if _shell_instance is not None:
        try:
            _shell_instance.close()
        except Exception:
            pass
        _shell_instance = None
    if _shell_pool is not None:
        try:
            _shell_pool.close_all()
        except Exception:
            pass
        _shell_pool = None


atexit.register(_close_global_shells)
