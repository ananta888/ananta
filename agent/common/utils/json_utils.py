import json
import logging
import os
import time
from typing import Any, Callable, Optional

import portalocker
import portalocker.exceptions

from agent.common.errors import PermanentError, TransientError


def read_json(path: str, default: Any = None) -> Any:
    """Liest eine JSON-Datei sicher mit File-Locking."""
    if not os.path.exists(path):
        return default

    retries = 3
    for i in range(retries):
        try:
            with portalocker.Lock(
                path, mode="r", encoding="utf-8", timeout=2, flags=portalocker.LOCK_SH | portalocker.LOCK_NB
            ) as f:
                return json.load(f)
        except (portalocker.exceptions.LockException, portalocker.exceptions.AlreadyLocked):
            if i < retries - 1:
                logging.warning(f"Datei {path} gesperrt, Retry {i + 1}/{retries}...")
                time.sleep(0.5)
                continue
            logging.error(f"Timeout beim Sperren von {path} nach {retries} Versuchen.")
            raise TransientError(f"Datei {path} ist dauerhaft gesperrt.")
        except Exception as e:
            logging.error(f"Fehler beim Lesen von {path}: {e}")
            return default


def write_json(path: str, data: Any, chmod: Optional[int] = None) -> None:
    """Schreibt eine JSON-Datei sicher mit File-Locking."""
    os.makedirs(os.path.dirname(path), exist_ok=True)

    retries = 3
    for i in range(retries):
        try:
            if chmod is not None and not os.path.exists(path):
                try:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT, chmod)
                    os.close(fd)
                except Exception:
                    pass

            with portalocker.Lock(
                path, mode="w", encoding="utf-8", timeout=2, flags=portalocker.LOCK_EX | portalocker.LOCK_NB
            ) as f:
                json.dump(data, f, indent=2)
                if chmod is not None:
                    try:
                        os.chmod(path, chmod)
                    except Exception:
                        pass
                return
        except (portalocker.exceptions.LockException, portalocker.exceptions.AlreadyLocked):
            if i < retries - 1:
                logging.warning(f"Datei {path} für Schreibzugriff gesperrt, Retry {i + 1}/{retries}...")
                time.sleep(0.5)
                continue
            logging.error(f"Timeout beim Sperren (Schreiben) von {path} nach {retries} Versuchen.")
            raise TransientError(f"Datei {path} konnte nicht geschrieben werden (Sperre).")
        except Exception as e:
            logging.error(f"Fehler beim Schreiben von {path}: {e}")
            raise PermanentError(f"Kritischer Fehler beim Schreiben von {path}: {e}")


def update_json(path: str, update_func: Callable[[Any], Any], default: Any = None) -> Any:
    """Führt einen atomaren Read-Modify-Write Zyklus auf einer JSON-Datei aus."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    retries = 10
    for i in range(retries):
        try:
            with portalocker.Lock(
                path, mode="a+", encoding="utf-8", timeout=5, flags=portalocker.LOCK_EX | portalocker.LOCK_NB
            ) as f:
                f.seek(0)
                content = f.read()
                data = default
                if content.strip():
                    try:
                        data = json.loads(content)
                    except (json.JSONDecodeError, ValueError) as e:
                        logging.warning(f"Konnte JSON aus {path} nicht lesen ({e}), verwende Default.")

                updated_data = update_func(data)

                f.seek(0)
                f.truncate()
                json.dump(updated_data, f, indent=2)
                f.flush()
                return updated_data
        except (portalocker.exceptions.LockException, portalocker.exceptions.AlreadyLocked):
            if i < retries - 1:
                logging.warning(f"Datei {path} für atomares Update gesperrt, Retry {i + 1}/{retries}...")
                time.sleep(0.5)
                continue
            logging.error(f"Timeout beim Sperren (Update) von {path} nach {retries} Versuchen.")
            raise TransientError(f"Datei {path} konnte nicht atomar aktualisiert werden.")
        except Exception as e:
            logging.error(f"Fehler beim atomaren Update von {path}: {e}")
            raise PermanentError(f"Kritischer Fehler beim Update von {path}: {e}")
