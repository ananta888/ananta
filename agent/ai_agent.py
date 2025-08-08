"""
agent/ai_agent.py

Überarbeitete Implementierung des Agent-Entrypoints mit verbessertem Configuration-Prioritätsmodell,
robustem Retry/Backoff für Datenbank-Init und Controller-Waiting, Signal-Handling für sauberen Shutdown,
Maskierung sensibler Werte in Logs und besserer Testbarkeit durch klar getrennte Hilfsfunktionen.

Wichtige Hinweise:
- Geheimnisse (z. B. OPENAI_API_KEY, DATABASE_URL) werden niemals im Klartext geloggt; nur maskierte Formen.
- Konfigurationspriorität: Funktion-Parameter > Umgebungsvariablen > config.json (ConfigManager) > Defaults.
- Keine externen Bibliotheken verwendet (nur Standardbibliothek + vorhandene Projekt-APIs).
- Falls einige externe Helfer (ConfigManager, LogManager, init_db, check_controller_connection, ModelPool,
  DEFAULT_ENDPOINTS) nicht importierbar sind, wird das Verhalten möglichst robust gehandhabt (mit Warnungen),
  um Fehlstarts zu vermeiden. In produktiver Umgebung sollten die genannten APIs verfügbar sein.
"""

from __future__ import annotations

import copy
import importlib
import json
import logging
import os
import platform
import re
import signal
import sys
import threading
import time
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from flask import Flask, jsonify

from src.db import get_conn
from .health import health_bp

# ---------- Flexible Import Helpers (versuchen mehrere mögliche Orte, robust gegenüber Projektstruktur) ----------
def _try_import_attr(attr_name: str, module_candidates: Iterable[str]):
    """
    Versucht nacheinander, ein Attribut aus mehreren Modulen zu importieren.
    Gibt das Attribut zurück oder None, falls nichts gefunden wurde.
    """
    for mod_name in module_candidates:
        try:
            mod = importlib.import_module(mod_name)
            if hasattr(mod, attr_name):
                return getattr(mod, attr_name)
        except Exception:
            # Importfehler überspringen und weiter versuchen
            continue
    return None


# Mögliche Modulpfade (erweiterbar)
_CONFIG_CANDS = ("src.config.manager", "config.manager", "src.config", "config", "src.config_manager", "config_manager")
_LOG_CANDS = ("src.log", "log", "log_manager", "src.logging", "logging_helper")
_DB_CANDS = ("src.db", "db", "database", "src.database")
_NET_CANDS = ("src.net", "network", "src.network", "utils.network")
_MODELPOOL_CANDS = ("src.model_pool", "model_pool", "models.pool", "src.models.pool")
_CONST_CANDS = ("src.constants", "constants", "src.defaults", "defaults")

ConfigManager = _try_import_attr("ConfigManager", _CONFIG_CANDS)
LogManager = _try_import_attr("LogManager", _LOG_CANDS)
init_db = _try_import_attr("init_db", _DB_CANDS)
check_controller_connection = _try_import_attr("check_controller_connection", _NET_CANDS)
ModelPool = _try_import_attr("ModelPool", _MODELPOOL_CANDS)
DEFAULT_ENDPOINTS = _try_import_attr("DEFAULT_ENDPOINTS", _CONST_CANDS)

# Falls LogManager nicht vorhanden ist, stellen wir eine sehr kleine Kompatibilitätsschicht bereit,
# die lediglich sicherstellt, dass LogManager.setup(name) aufgerufen werden kann.
if LogManager is None:
    class _FallbackLogManager:
        @staticmethod
        def setup(name: str) -> None:
            # Nichts tun; wir konfigurieren logging weiter unten direkt.
            pass
    LogManager = _FallbackLogManager  # type: ignore

# DEFAULT_ENDPOINTS darf nicht None sein; liefere leere Dict als Fallback
if DEFAULT_ENDPOINTS is None:
    DEFAULT_ENDPOINTS = {}

# ---------- Hilfsfunktionen (rein funktional / testbar) ----------

def mask_secret(secret: Optional[str], show_last: int = 4) -> str:
    """
    Maskiert ein Geheimnis, sodass nur die letzten `show_last` Zeichen sichtbar bleiben.
    Gibt "<MISSING>" zurück, wenn secret None oder leer ist.
    Beispiele:
        "abcd1234" -> "****1234" (bei show_last=4)
    """
    if not secret:
        return "<MISSING>"
    s = str(secret)
    if len(s) <= show_last:
        return "*" * max(1, len(s) - 1) + s[-1:]
    return "*" * (len(s) - show_last) + s[-show_last:]


def _normalize_url(url: str) -> Optional[str]:
    """
    Normalisiert eine URL:
      - Entfernt mehrfache Slashes im Pfad
      - Vereinheitlicht das Fehlen/vorhandensein eines abschließenden Slash (wir entfernen ihn)
      - Validiert das Schema (nur http/https akzeptiert)
    Gibt die normalisierte URL zurück oder None bei ungültiger URL.
    """
    try:
        url = url.strip()
        if not url:
            return None
        # Sicherstellen, dass ein Schema vorhanden ist, sonst keine Annahmen
        parsed = __import__("urllib.parse").parse.urlparse(url) if False else __import__("urllib.parse").urlparse(url)
    except Exception:
        # Fallback simple parse
        from urllib.parse import urlparse as _up
        parsed = _up(url)

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return None

    # Normalize path: replace multiple slashes with single slash
    path = re.sub(r"/{2,}", "/", parsed.path or "")
    # Entferne trailing slash (ausnahme: root)
    if path.endswith("/") and len(path) > 1:
        path = path[:-1]

    # Rebuild URL
    from urllib.parse import urlunparse
    normalized = urlunparse((scheme, parsed.netloc, path, "", parsed.query or "", parsed.fragment or ""))
    return normalized


def build_endpoint_map(
    base_defaults: Dict[str, str],
    cfg_endpoints: Optional[Iterable[Dict[str, str]]] = None,
    override_endpoints: Optional[Dict[str, str]] = None,
    env_prefix: str = "AI_AGENT_ENDPOINT_",
) -> Dict[str, str]:
    """
    Baut die finale Endpoint-Map mit folgender Priorität:
      1. base_defaults (kopiert)
      2. cfg_endpoints (Liste oder Dict aus config.json; erwartet Felder "type" und "url" bei Listeneinträgen)
      3. override_endpoints (argument)
      4. Umgebungsvariablen AI_AGENT_ENDPOINT_<TYPE> (überschreiben)
    Validiert und normalisiert URLs; ungültige URLs werden übersprungen und führen zu einer Warnung.

    Gibt eine Map type->url zurück.
    """
    result: Dict[str, str] = copy.deepcopy(base_defaults) if base_defaults else {}

    # 2) config entries
    if cfg_endpoints:
        # Unterstützung für dict oder iterable von dict
        if isinstance(cfg_endpoints, dict):
            for t, u in cfg_endpoints.items():
                if not t:
                    continue
                norm = _normalize_url(str(u)) if u else None
                if norm:
                    result[str(t)] = norm
        else:
            for entry in cfg_endpoints:
                if not isinstance(entry, dict):
                    continue
                t = entry.get("type") or entry.get("name")
                u = entry.get("url")
                if not t:
                    continue
                norm = _normalize_url(str(u)) if u else None
                if norm:
                    result[str(t)] = norm

    # 3) override endpoints (function param)
    if override_endpoints:
        for t, u in override_endpoints.items():
            if not t:
                continue
            norm = _normalize_url(str(u)) if u else None
            if norm:
                result[str(t)] = norm

    # 4) environment variables AI_AGENT_ENDPOINT_<TYPE>
    for k, v in os.environ.items():
        if not k.startswith(env_prefix):
            continue
        t = k[len(env_prefix):]
        if not t:
            continue
        norm = _normalize_url(v)
        if norm:
            result[str(t)] = norm

    # Warnung bei leeren oder duplizierten Typen handled vom Aufrufer, hier nur Rückgabe
    return result


def resolve_controller_url(
    controller_param: Optional[str],
    cfg: Optional[Dict] = None,
    env_var: str = "AI_AGENT_CONTROLLER_URL",
) -> Optional[str]:
    """
    Bestimmt die effektive Controller-URL nach der Priorität:
      1. controller_param
      2. Umgebungsvariable AI_AGENT_CONTROLLER_URL
      3. cfg["controller_url"]
      4. None
    Normalisiert und validiert das Schema (http/https). Gibt None zurück, falls keine gültige URL ermittelt werden kann.
    """
    candidates = []
    if controller_param:
        candidates.append(controller_param)
    env_val = os.environ.get(env_var)
    if env_val:
        candidates.append(env_val)
    if cfg and isinstance(cfg, dict):
        cfg_c = cfg.get("controller_url") or cfg.get("controller")
        if cfg_c:
            candidates.append(cfg_c)

    for cand in candidates:
        try:
            norm = _normalize_url(str(cand))
        except Exception:
            norm = None
        if norm:
            return norm
    return None


def wait_for_condition(
    name: str,
    fn_check: Callable[[], bool],
    timeout_s: float,
    base_delay_s: float = 0.5,
    max_delay_s: float = 30.0,
    stop_event: Optional[threading.Event] = None,
) -> bool:
    """
    Wartet darauf, dass fn_check() True zurückgibt, mit exponentiellem Backoff.
    - name: Bezeichnung für Logs
    - timeout_s: maximale Wartezeit in Sekunden
    - base_delay_s: Anfangswartezeit
    - max_delay_s: Maximaler Warteintervall
    - stop_event: Optionales threading.Event, das das Warten vorzeitig abbricht (wenn gesetzt)
    Rückgabe: True wenn Bedingung erfüllt, False bei Timeout oder Stop-Signal.
    """
    logger = logging.getLogger("agent")
    start = time.monotonic()
    attempt = 0
    delay = base_delay_s
    while True:
        if stop_event and stop_event.is_set():
            logger.info("%s: Abbruch durch Stop-Signal vor Abschluss.", name)
            return False

        attempt += 1
        try:
            ok = bool(fn_check())
        except Exception as exc:  # defensive
            logger.debug("%s: fn_check() hat Exception erzeugt (Versuch %d): %s", name, attempt, exc)
            ok = False

        if ok:
            logger.debug("%s: Bedingung erfüllt (Versuch %d).", name, attempt)
            return True

        elapsed = time.monotonic() - start
        if elapsed >= timeout_s:
            logger.warning("%s: Timeout nach %.1fs (Versuch %d).", name, elapsed, attempt)
            return False

        next_delay = min(delay, max_delay_s, timeout_s - elapsed)
        logger.info("%s: Versuch %d fehlgeschlagen, warte %.2fs bis zum nächsten Versuch...", name, attempt, next_delay)
        # Wait in interruptible manner
        if stop_event:
            stop_event.wait(next_delay)
            if stop_event.is_set():
                logger.info("%s: Abbruch durch Stop-Signal während Wartezeit.", name)
                return False
        else:
            time.sleep(next_delay)

        # Exponentielles Backoff erhöhen
        delay = min(delay * 2.0, max_delay_s)


# ---------- Logging Setup ----------
def _setup_logging(level_name: str) -> None:
    """
    Konfiguriert das Python-Logging entsprechend den Anforderungen.
    Unterstützt optional JSON-Format via AI_AGENT_LOG_JSON=true.
    """
    # Validierung Level
    level_name_up = (level_name or "INFO").upper()
    if level_name_up not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        level_name_up = "INFO"
    level = getattr(logging, level_name_up, logging.INFO)

    # Aufrufen des projektweiten LogManagers, falls vorhanden
    try:
        LogManager.setup("agent")
    except Exception:
        # Swallow exceptions from the external LogManager and continue with local setup
        pass

    # Grundkonfiguration
    logger = logging.getLogger("agent")
    logger.setLevel(level)
    # Entferne vorhandene Handler, um Einheitlichkeit zu gewährleisten
    for h in list(logger.handlers):
        logger.removeHandler(h)

    formatter = None
    json_mode = os.environ.get("AI_AGENT_LOG_JSON", "false").lower() in ("1", "true", "yes")
    if json_mode:
        # Einfacher JSON-Formatter
        def json_formatter(record: logging.LogRecord) -> str:
            payload = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
                "logger": record.name,
                "level": record.levelname,
                "msg": record.getMessage(),
            }
            # Extra-Felder (z.B. agent) falls vorhanden
            if hasattr(record, "agent"):
                payload["agent"] = record.agent
            return json.dumps(payload, ensure_ascii=False)

        class _JsonHandler(logging.StreamHandler):
            def format(self, record):
                return json_formatter(record)

        handler = _JsonHandler(stream=sys.stdout)
    else:
        fmt = "%(asctime)s %(name)s %(levelname)s: %(message)s"
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(logging.Formatter(fmt=fmt, datefmt="%Y-%m-%d %H:%M:%S"))

    logger.addHandler(handler)


# ---------- Hauptfunktion (Kompatible, erweiterbare Signatur) ----------
def run_agent(
    controller: Optional[str] = None,
    endpoints: Optional[Dict[str, str]] = None,
    config_path: str = "config.json",
    steps: Optional[int] = None,
    step_delay: float = 1.0,
    pool: Optional[object] = None,
    openai_api_key: Optional[str] = None,
    database_url: Optional[str] = None,
    log_level: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
    *,
    # Parameter für deterministic Backoff in Tests
    db_backoff_base: float = 0.5,
    db_backoff_max: float = 30.0,
    db_total_timeout: float = 120.0,
    controller_wait_timeout: float = 300.0,
) -> None:
    """
    Startet den Agenten mit robustem Setup und Laufzeitverhalten.

    Parameter (häufige / kompatible):
      - controller: Optionale Controller-URL (höchste Priorität).
      - endpoints: Optionale Endpoint-Override-Map {type: url}.
      - config_path: Pfad zu config.json (wird an ConfigManager übergeben).
      - steps: Anzahl Iterationen; None = unendlich bis Stop-Signal.
      - step_delay: Sekunden zwischen Iterationen (float, 0 bedeutet kein Warten).
      - pool: Optionaler ModelPool (wird im Finally block sauber geschlossen, falls unterstützte Methoden vorhanden sind).
      - openai_api_key: Optional, zur Maskierung/Validierung; wird niemals im Klartext geloggt.
      - database_url: Optional, nur für Validierung und Maskierung in Logs.
      - log_level: Optionaler Log-Level (DEBUG, INFO, WARNING, ERROR).
      - stop_event: Optionales threading.Event zum externen Stoppen; wird erzeugt, falls None.
      - db_backoff_base, db_backoff_max, db_total_timeout: Backoff-Parameter für DB-Initialisierung (nützlich für Tests).
      - controller_wait_timeout: Gesamttimeout für Controller-Wait (Sekunden).

    Rückgabe:
      - None. Bei fatalen Fehlern werden Ausnahmen geworfen (RuntimeError).
    """
    # 1) Config laden (falls vorhanden)
    cfg: Dict = {}
    if ConfigManager:
        try:
            cfg_mgr = ConfigManager(config_path)
            cfg_read = {}
            try:
                cfg_read = cfg_mgr.read() or {}
            except Exception as exc:
                # defensiv, ConfigManager kann fehlschlagen; wir loggen später
                cfg_read = {}
            cfg = cfg_read
        except Exception:
            cfg = {}
    else:
        cfg = {}

    # 2) Kombiniere Log-Level Prioritäten: param > ENV > config.json > default
    effective_log_level = (
        (log_level if log_level else os.environ.get("AI_AGENT_LOG_LEVEL") or cfg.get("log_level") or "INFO")
    )
    _setup_logging(effective_log_level)
    logger = logging.getLogger("agent")

    # Setup Stop-Event & Signal-Handler
    internal_stop = stop_event or threading.Event()

    def _signal_handler(signum, frame):
        logger.info("Empfange Signal %s, löse sauberen Shutdown aus.", signum)
        internal_stop.set()

    # Register handlers (SIGINT, SIGTERM)
    try:
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
    except Exception:
        # In eingeschränkten Umgebungen (z. B. Windows Threads) kann signal nicht gesetzt werden; logge und weiter
        logger.debug("Signal-Handler konnten nicht registriert werden (Umgebung beschränkt).")

    # 3) Maskiere sensible Umgebungswerte für Logs (nicht im Klartext ausgeben)
    env_openai = openai_api_key or os.environ.get("OPENAI_API_KEY") or cfg.get("openai_api_key")
    masked_openai = mask_secret(env_openai)
    env_db = database_url or os.environ.get("DATABASE_URL") or cfg.get("database_url")
    masked_db = mask_secret(env_db)

    # 4) Erzeuge finalen Endpoint-Map gemäß Prioritäten
    cfg_api_endpoints = cfg.get("api_endpoints")
    try:
        endpoint_map = build_endpoint_map(DEFAULT_ENDPOINTS, cfg_api_endpoints, endpoints)
    except Exception as exc:
        logger.warning("Fehler beim Aufbau der Endpoint-Map: %s", exc)
        endpoint_map = copy.deepcopy(DEFAULT_ENDPOINTS)

    # 5) Ermittele Controller-URL mit Priorität: parameter > ENV > cfg
    effective_controller = resolve_controller_url(controller, cfg)
    if not effective_controller:
        logger.warning(
            "Keine valide Controller-URL ermittelt (Parameter/ENV/Config). "
            "Es wird weiterhin versucht, eine Verbindung aufzubauen."
        )
    # Log Start-Zusammenfassung (ohne Geheimnisse)
    try:
        logger.info(
            "Agent-Start: Controller=%s, Endpunkte=%d, Python=%s",
            effective_controller or "<UNSET>",
            len(endpoint_map),
            platform.python_version(),
        )
        logger.debug("Masked OPENAI_API_KEY=%s, DATABASE_URL=%s", masked_openai, masked_db)
    except Exception:
        # Defensive logging failure should not stop execution
        pass

    # 6) Datenbank initialisieren mit Retry/Backoff
    if init_db:
        db_start = time.monotonic()
        attempt = 0
        delay = db_backoff_base
        success_db = False
        while True:
            if internal_stop.is_set():
                logger.info("Abbruch der DB-Initialisierung durch Stop-Signal.")
                break
            attempt += 1
            try:
                logger.info("DB-Initialisierung: Versuch %d...", attempt)
                # Falls init_db() ein Ergebnis zurückgibt, wirft es ggf. Exceptions bei Fehler
                init_db()
                success_db = True
                logger.info("DB-Initialisierung erfolgreich nach %d Versuch(en).", attempt)
                break
            except Exception as exc:
                elapsed = time.monotonic() - db_start
                if elapsed >= db_total_timeout:
                    logger.error("DB-Initialisierung scheiterte nach %.1fs und %d Versuchen: %s", elapsed, attempt, exc)
                    # Endgültiges Scheitern
                    raise RuntimeError("Datenbank-Initialisierung fehlgeschlagen") from exc
                # Logge, maskiere DB-URL (kein Klartext)
                logger.warning("DB-Initialisierung Versuch %d fehlgeschlagen: %s. Warte %.2fs vor erneutem Versuch...",
                               attempt, getattr(exc, "args", exc), delay)
                # Unterbrechbares Warten
                internal_stop.wait(delay)
                if internal_stop.is_set():
                    logger.info("Abbruch der DB-Initialisierung durch Stop-Signal während Wartezeit.")
                    break
                delay = min(delay * 2.0, db_backoff_max)
        if not success_db:
            # Wenn nicht erfolgreich, je nach Policy abbrechen. Wir wählen hier Abbruch.
            logger.error("Datenbank-Initialisierung konnte nicht abgeschlossen werden; beende Agent.")
            return
    else:
        logger.warning("Kein init_db() verfügbar; überspringe Datenbank-Initialisierung (prüfen Sie Projekt-Setup).")

    # 7) Warten auf Controller-Verfügbarkeit mit Retry (interruptible)
    if effective_controller and check_controller_connection:
        logger.info("Prüfe Erreichbarkeit des Controllers unter %s ...", effective_controller)

        def _check() -> bool:
            try:
                return bool(check_controller_connection(effective_controller))
            except Exception:
                return False

        ok = wait_for_condition(
            name="Controller-Connection",
            fn_check=_check,
            timeout_s=controller_wait_timeout,
            base_delay_s=0.5,
            max_delay_s=30.0,
            stop_event=internal_stop,
        )
        if not ok:
            logger.error("Controller %s ist nach Wartezeit nicht erreichbar.", effective_controller)
            return
    elif not effective_controller:
        logger.warning("Controller-URL unbekannt; überspringe Erreichbarkeitsprüfung.")
    else:
        logger.warning("Keine Funktion check_controller_connection verfügbar; überspringe Controller-Prüfung.")

    # 8) Hauptschleife
    logger.info("Starte Hauptschleife (steps=%s, step_delay=%.3fs).", str(steps), float(step_delay))
    iteration = 0
    try:
        # Unterstütze sowohl None (unendlich) als auch int steps
        while True:
            if internal_stop.is_set():
                logger.info("Stop-Signal empfangen, beende Hauptschleife.")
                break

            if steps is not None and iteration >= int(steps):
                logger.info("Erreichte gewünschte Schrittanzahl (%d). Beende Hauptschleife.", steps)
                break

            iteration += 1
            it_start = time.perf_counter()
            logger.debug("Iteration %d gestartet; verbleibende Schritte: %s", iteration,
                         ("unendlich" if steps is None else str(max(0, int(steps) - iteration))))

            try:
                # Hier würde die eigentliche Agentenarbeit stattfinden.
                # Da wir nur die Struktur überarbeiten sollen, simulieren wir einen Arbeitsschritt
                # mit einem kurzen Debug-Log. In der realen Implementierung rufen Sie die relevanten
                # Agent-Methoden/Modelle auf.
                logger.debug("Iteration %d: Arbeitslogik ausführen (Platzhalter).", iteration)
            except Exception as exc:
                # Jede Iteration darf Fehler haben; loggen und weitermachen (sofern kein Stop-Signal)
                logger.exception("Fehler in Iteration %d: %s", iteration, exc)

            it_dur = time.perf_counter() - it_start
            logger.info("Iteration %d abgeschlossen (Dauer %.3fs).", iteration, it_dur)

            # Schlafe nur, wenn step_delay > 0 und keine Stop-Bedingung
            if step_delay and (not internal_stop.is_set()):
                # Verwende Event.wait, damit wir auf Stop-Signal reagieren können
                internal_stop.wait(step_delay)
    finally:
        # 9) Sauberes Aufräumen in finally-Block (ModelPool oder ähnliche Ressourcen)
        try:
            if pool:
                # Unterstütze verschiedene cleanup-Methoden, falls vorhanden
                for meth in ("close", "shutdown", "cleanup", "stop"):
                    if hasattr(pool, meth) and callable(getattr(pool, meth)):
                        try:
                            getattr(pool, meth)()
                            logger.info("ModelPool: Methode %s aufgerufen.", meth)
                        except Exception as exc:
                            logger.warning("Fehler beim Aufräumen des ModelPool mit %s: %s", meth, exc)
                        break
                else:
                    logger.debug("ModelPool-Objekt vorhanden, aber keine bekannte Aufräummethode gefunden.")
            elif ModelPool and isinstance(ModelPool, type):
                # Falls kein pool übergeben, versuchen wir, falls ModelPool instanziierbar ist, kurz aufzuräumen.
                # Dies ist optional und defensiv: nur wenn pool=None und ModelPool eine Klasse ist, ignorieren wir.
                pass
        except Exception as exc:
            logger.warning("Fehler während Cleanup: %s", exc)

    logger.info("Agent beendet.")


def create_app(agent_name: str = "default") -> Flask:
    """Create a minimal Flask application exposing agent endpoints."""

    app = Flask(__name__)
    app.register_blueprint(health_bp)

    @app.route("/logs")
    def logs() -> object:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT level, message FROM agent.logs WHERE agent=%s ORDER BY id",
            (agent_name,),
        )
        rows = [{"level": r[0], "message": r[1]} for r in cur.fetchall()]
        cur.close()
        conn.close()
        return jsonify({"agent": agent_name, "logs": rows})

    @app.route("/tasks")
    def tasks() -> object:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT data FROM controller.config ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        current = None
        if row and isinstance(row[0], dict):
            current = (
                row[0]
                .get("agents", {})
                .get(agent_name, {})
                .get("current_task")
            )
        cur.execute(
            "SELECT task, agent, template FROM controller.tasks "
            "WHERE agent=%s OR agent IS NULL ORDER BY id",
            (agent_name,),
        )
        tasks = [
            {"task": r[0], "agent": r[1], "template": r[2]} for r in cur.fetchall()
        ]
        cur.close()
        conn.close()
        return jsonify({"agent": agent_name, "current_task": current, "tasks": tasks})

    @app.route("/stop", methods=["POST"])
    def stop() -> object:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO agent.flags (name, value) VALUES ('stop','1') "
            "ON CONFLICT (name) DO UPDATE SET value='1'",
        )
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"status": "ok"})

    @app.route("/restart", methods=["POST"])
    def restart() -> object:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM agent.flags WHERE name='stop'")
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"status": "ok"})

    return app
