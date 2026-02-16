import subprocess
import threading
import os
import time
import logging
import uuid
import re
from queue import Queue, Empty, Full
from typing import List

try:
    from agent.config import settings
    from agent.metrics import SHELL_POOL_SIZE, SHELL_POOL_BUSY, SHELL_POOL_FREE
except (ImportError, ModuleNotFoundError):
    # Falls wir direkt im agent-Ordner sind
    try:
        from config import settings
        from metrics import SHELL_POOL_SIZE, SHELL_POOL_BUSY, SHELL_POOL_FREE
    except (ImportError, ModuleNotFoundError):
        # Fallback wenn metrics nicht da ist (sollte nicht passieren)
        class MockMetric:
            def set(self, val):
                pass

        SHELL_POOL_SIZE = SHELL_POOL_BUSY = SHELL_POOL_FREE = MockMetric()
        # Settings fallback falls auch config fehlt
        if "settings" not in locals():

            class MockSettings:
                shell_path = None
                shell_pool_size = 5

            settings = MockSettings()


class PersistentShell:
    def __init__(self, shell_cmd: str = None):
        if shell_cmd is None:
            shell_cmd = settings.shell_path

        if shell_cmd is None:
            if os.name == "nt":
                shell_cmd = "cmd.exe"
            else:
                # PrÃ¼fe ob bash verfÃ¼gbar ist, sonst sh
                import shutil

                if shutil.which("bash"):
                    shell_cmd = "bash"
                else:
                    shell_cmd = "sh"

        self.shell_cmd = shell_cmd
        self.is_powershell = "powershell" in shell_cmd.lower() or "pwsh" in shell_cmd.lower()
        self.process = None
        self.lock = threading.Lock()
        self.output_queue = Queue()
        self.reader_thread = None
        self.blacklist = []
        self.blacklist_mtime = 0
        self._load_blacklist()
        self._start_process()

    def _load_blacklist(self):
        # Suche blacklist.txt im Hauptordner (ein Level Ã¼ber agent/) oder im aktuellen Arbeitsverzeichnis
        possible_paths = [
            os.path.join(os.getcwd(), "blacklist.txt"),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "blacklist.txt"),
        ]
        for path in possible_paths:
            if os.path.exists(path):
                try:
                    mtime = os.path.getmtime(path)
                    if mtime > self.blacklist_mtime:
                        with open(path, "r") as f:
                            self.blacklist = [
                                line.strip() for line in f if line.strip() and not line.strip().startswith("#")
                            ]
                        self.blacklist_mtime = mtime
                        logging.info(f"Blacklist geladen ({len(self.blacklist)} EintrÃ¤ge) von {path}")
                    break
                except Exception as e:
                    logging.error(f"Fehler beim Laden der Blacklist von {path}: {e}")

    def _start_process(self):
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass

        cmd = [self.shell_cmd]

        if os.name == "nt":
            if self.shell_cmd == "cmd.exe":
                cmd = [self.shell_cmd, "/q", "/k"]
            elif self.is_powershell:
                cmd = [self.shell_cmd, "-NoLogo", "-NoExit", "-Command", "-"]
        else:
            # FÃ¼r Linux/Unix Shells: PersistentShell nutzt stdin/stdout piping.
            # Wir verzichten auf den interaktiven Modus (-i), da dieser in Docker-Containern
            # ohne TTY oft zu Problemen fÃ¼hrt oder hÃ¤ngen bleibt.
            cmd = [self.shell_cmd]

        try:
            logging.info(f"Starte Shell-Prozess: {cmd}")
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                shell=False,
                env=os.environ.copy(),  # Env explizit weitergeben
            )
        except Exception as e:
            logging.error(f"Konnte Shell-Prozess '{self.shell_cmd}' nicht starten: {e}")
            if self.shell_cmd != "sh" and os.name != "nt":
                logging.info("Versuche Fallback auf /bin/sh")
                self.shell_cmd = "sh"
                return self._start_process()
            raise

        # Start reader thread
        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()

        # Initial wait to clear the welcome message of the shell
        if os.name == "nt":
            if self.shell_cmd == "cmd.exe":
                self.execute("echo off")  # Reduce noise
            elif self.is_powershell:
                self.execute("$ProgressPreference = 'SilentlyContinue'")  # Reduce noise

    def _read_output(self):
        while self.process and self.process.stdout:
            line = self.process.stdout.readline()
            if line:
                self.output_queue.put(line)
            else:
                if self.process.poll() is not None:
                    break
                time.sleep(0.1)

    def execute(self, command: str, timeout: int = 30) -> tuple[str, int | None]:
        # 1. Blacklist-PrÃ¼fung mittels Regex (Gesamtstring)
        self._load_blacklist()
        for pattern in self.blacklist:
            try:
                if re.search(pattern, command, re.IGNORECASE):
                    logging.warning(f"GefÃ¤hrlicher Befehl blockiert: {command} (Match mit Pattern '{pattern}')")
                    return f"Error: Command matches blacklisted pattern '{pattern}'", -1
            except re.error as e:
                if pattern in command:
                    logging.warning(f"GefÃ¤hrlicher Befehl blockiert: {command} (enthÃ¤lt '{pattern}')")
                    return f"Error: Command contains blacklisted pattern '{pattern}'", -1
                logging.error(f"UngÃ¼ltiges Regex-Pattern in Blacklist: {pattern} ({e})")

        # 2. Token-basierte PrÃ¼fung (gegen Argument-Injektion)
        is_safe_tokens, reason_tokens = self._validate_tokens(command)
        if not is_safe_tokens:
            logging.warning(f"Befehl durch Token-PrÃ¼fung blockiert: {command}. Grund: {reason_tokens}")
            return f"Error: {reason_tokens}", -1

        # 3. PrÃ¼fung auf Command Substitution und gefÃ¤hrliche Metazeichen
        is_safe_meta, reason_meta = self._validate_meta_characters(command)
        if not is_safe_meta:
            logging.warning(f"Befehl durch Metazeichen-PrÃ¼fung blockiert: {command}. Grund: {reason_meta}")
            return f"Error: {reason_meta}", -1

        # 4. Advanced Command Analysis mittels LLM (optional)
        if settings.enable_advanced_command_analysis:
            is_safe, reason = self._analyze_command_intent(command)
            if not is_safe:
                logging.warning(f"Befehl durch LLM-Analyse blockiert: {command}. Grund: {reason}")
                return f"Error: Command blocked by LLM analysis. Reason: {reason}", -1

        with self.lock:
            if not self.process or self.process.poll() is not None:
                self._start_process()

            # Clear the queue before executing a new command
            while not self.output_queue.empty():
                try:
                    self.output_queue.get_nowait()
                except Empty:
                    break

            current_marker = f"---CMD_FINISHED_{uuid.uuid4()}---"

            if os.name == "nt":
                if self.is_powershell:
                    # Verbesserte Fehlererkennung fÃ¼r PowerShell:
                    # 1. $Error.Clear() um alte Fehler zu entfernen
                    # 2. AusfÃ¼hrung des Befehls
                    # 3. $? prÃ¼fen (True wenn erfolgreich)
                    # 4. $LASTEXITCODE fÃ¼r externe Prozesse prÃ¼fen
                    # 5. $Error.Count als Fallback fÃ¼r Cmdlet-Fehler
                    full_command = (
                        f"$Error.Clear(); {command}; "
                        f"$lsc = if($?) {{ 0 }} else {{ 1 }}; "
                        f"if($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) {{ $lsc = $LASTEXITCODE }}; "
                        f"if($Error.Count -gt 0 -and $lsc -eq 0) {{ $lsc = 1 }}; "
                        f'echo "{current_marker} $lsc"\n'
                    )
                else:
                    full_command = f"{command}\necho {current_marker} %ERRORLEVEL%\n"
            else:
                full_command = f"{command}\necho {current_marker} $?\n"

            try:
                self.process.stdin.write(full_command)
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                self._start_process()
                self.process.stdin.write(full_command)
                self.process.stdin.flush()

            output = []
            start_time = time.time()
            exit_code = 0

            while True:
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    logging.warning(f"Timeout bei BefehlsausfÃ¼hrung: {command}")
                    return "".join(output) + "\n[Error: Timeout]", -1

                try:
                    line = self.output_queue.get(timeout=max(0.1, timeout - elapsed))
                except Empty:
                    if self.process.poll() is not None:
                        # Prozess ist abgestÃ¼rzt oder wurde beendet
                        logging.error(f"Shell-Prozess unerwartet beendet wÃ¤hrend: {command}")
                        return "".join(output) + "\n[Error: Shell process terminated unexpectedly]", -1
                    continue

                if current_marker in line:
                    try:
                        parts = line.strip().split(" ")
                        if len(parts) > 1:
                            exit_code = int(parts[-1])
                    except (ValueError, IndexError) as e:
                        logging.warning(f"Konnte Exit-Code nicht parsen: {e}")
                    break
                output.append(line)

            return "".join(output).strip(), exit_code

    def is_healthy(self) -> bool:
        """PrÃ¼ft, ob der Shell-Prozess noch lÃ¤uft und reagiert."""
        with self.lock:
            if not self.process or self.process.poll() is not None:
                return False
            # Optional: Hier kÃ¶nnte man noch einen echo-Test machen,
            # aber das wÃ¤re teuer vor jeder Nutzung.
            return True

    def _validate_tokens(self, command: str) -> tuple[bool, str]:
        """PrÃ¼ft einzelne Tokens eines Befehls gegen die Blacklist."""
        # Dynamische PrÃ¼fung auf sensible Verzeichnisse
        sensitive_patterns = [r"\.git/", r"secrets/", r"\.env", r"token\.json"]
        for sp in sensitive_patterns:
            if re.search(sp, command, re.IGNORECASE):
                # Wenn es kein reiner Lese-Befehl ist (sehr vereinfacht)
                if not any(cmd in command.lower() for cmd in ["ls ", "cat ", "type ", "dir "]):
                    return False, f"Schreibzugriff auf sensiblen Pfad blockiert: {sp}"

        try:
            import shlex

            tokens = []
            if self.is_powershell:
                # Verbesserte Tokenisierung fÃ¼r PowerShell
                # PowerShell nutzt ` als Escape-Zeichen und hat andere Metazeichen
                current_token = []
                in_double_quote = False
                in_single_quote = False
                i = 0
                while i < len(command):
                    char = command[i]
                    # PowerShell Escape-Zeichen (Backtick)
                    if char == "`" and i + 1 < len(command):
                        current_token.append(command[i + 1])
                        i += 2
                        continue

                    if char == '"' and not in_single_quote:
                        in_double_quote = not in_double_quote
                        current_token.append(char)
                    elif char == "'" and not in_double_quote:
                        in_single_quote = not in_single_quote
                        current_token.append(char)
                    elif not in_double_quote and not in_single_quote:
                        # PowerShell Trenner: Leerzeichen, Tabs, Semikolons, Pipes, etc.
                        if char in " \t\n\r;|^&(){}[]":
                            if current_token:
                                tokens.append("".join(current_token))
                                current_token = []
                            if char.strip():  # Behalte Metazeichen als eigene Tokens (auÃŸer Whitespace)
                                tokens.append(char)
                        else:
                            current_token.append(char)
                    else:
                        current_token.append(char)
                    i += 1
                if current_token:
                    tokens.append("".join(current_token))
            else:
                # Standard shlex fÃ¼r andere Shells (bash, cmd)
                if os.name == "nt":
                    tokens = shlex.split(command, posix=False)
                else:
                    tokens = shlex.split(command)

            for token in tokens:
                # Bereinige Token von AnfÃ¼hrungszeichen fÃ¼r die PrÃ¼fung
                clean_token = token.strip("'\"")
                if not clean_token:
                    continue

                # PrÃ¼fe Token gegen Blacklist
                for pattern in self.blacklist:
                    try:
                        # Wir prÃ¼fen auf Wortgrenzen fÃ¼r kurze Befehle in der Blacklist
                        # oder auf exakte Matches/Regex
                        if re.search(pattern, clean_token, re.IGNORECASE):
                            return False, f"GefÃ¤hrlicher Token erkannt: '{clean_token}' (Match mit '{pattern}')"
                    except re.error:
                        if pattern in clean_token:
                            return False, f"GefÃ¤hrlicher Token erkannt: '{clean_token}' (enthÃ¤lt '{pattern}')"

            return True, ""
        except Exception as e:
            # Bei Parser-Fehlern blockieren wir sicherheitshalber
            return False, f"Befehls-Analyse fehlgeschlagen: {e}"

    def _validate_meta_characters(self, command: str) -> tuple[bool, str]:
        """Prueft auf gefaehrliche Shell-Metazeichen und Command Substitution."""
        if "`n" in command or "`r" in command:
            return False, "Mehrzeilige Befehle sind aus Sicherheitsgruenden deaktiviert."

        # Command Substitution: `command` oder $(command)
        if "`" in command:
            return False, "Backticks (`) sind aus Sicherheitsgruenden deaktiviert."

        if "$(" in command:
            return False, "Command Substitution $() ist aus Sicherheitsgruenden deaktiviert."

        # Schutz gegen Variablen-Verkettung (z.B. $a$b oder ${a}${b})
        # Dies wird oft genutzt, um Blacklists zu umgehen.
        if re.search(r"\$\w+\$", command) or re.search(r"\}\$\{", command):
            return False, "Variablen-Verkettung ($a$b) ist aus Sicherheitsgr\u00fcnden deaktiviert."

        # Gefaehrliche Verkettungen deaktivieren.
        if ";" in command:
            return False, "Semikolons (;) sind als Befehlstrenner deaktiviert."
        if "&&" in command or "||" in command:
            return False, "Befehlskettung (&&/||) ist aus Sicherheitsgruenden deaktiviert."

        # Redirection/Background-Ausfuehrung kann Sicherheitsmechanismen umgehen.
        if re.search(r"(^|[^>])>([^>]|$)", command) or "<" in command:
            return False, "Input/Output-Redirection ist aus Sicherheitsgruenden deaktiviert."
        if re.search(r"(^|[^&])&([^&]|$)", command):
            return False, "Background-Execution (&) ist aus Sicherheitsgruenden deaktiviert."

        return True, ""

    def _analyze_command_intent(self, command: str) -> tuple[bool, str]:
        """Nutzt ein LLM, um die Intention eines Befehls zu analysieren."""
        try:
            from agent.llm_integration import _call_llm
            import json

            prompt = (
                f"Analysiere den folgenden Shell-Befehl auf bÃ¶sartige Absichten oder extreme GefÃ¤hrlichkeit "
                "(z.B. LÃ¶schen des gesamten Systems, Ã„ndern von Admin-PasswÃ¶rtern, "
                "Exfiltration sensibler Daten):\n\n"
                f"Befehl: {command}\n\n"
                f"Antworte NUR in folgendem JSON-Format:\n"
                f"{{\n"
                f'  "safe": true/false,\n'
                f'  "reason": "BegrÃ¼ndung hier"\n'
                f"}}"
            )

            # Wir nutzen die Default-Einstellungen fÃ¼r die Analyse
            urls = {
                "ollama": settings.ollama_url,
                "lmstudio": settings.lmstudio_url,
                "openai": settings.openai_url,
                "anthropic": settings.anthropic_url,
            }

            res_raw = _call_llm(
                provider=settings.default_provider,
                model=settings.default_model,
                prompt=prompt,
                urls=urls,
                api_key=settings.openai_api_key,
            )

            # Versuche das JSON zu parsen
            try:
                # Manchmal packt das LLM den Output in Markdown-Code-Blocks
                res_clean = res_raw.strip()
                if res_clean.startswith("```"):
                    res_clean = res_clean.split("```")[1]
                    if res_clean.startswith("json"):
                        res_clean = res_clean[4:].strip()

                data = json.loads(res_clean)
                safe = data.get("safe")
                if isinstance(safe, str):
                    safe = safe.lower() == "true"
                elif safe is None:
                    safe = True

                return safe, data.get("reason", "Keine BegrÃ¼ndung angegeben")
            except Exception as e:
                logging.error(f"Fehler beim Parsen der LLM-Analyse: {e}. Raw: {res_raw}")
                # Falls die Analyse fehlschlÃ¤gt, entscheiden wir basierend auf Fail-Secure
                if getattr(settings, "fail_secure_llm_analysis", False):
                    return False, f"Analyse fehlgeschlagen (Parser-Fehler), Fail-Secure aktiv. Fehler: {e}"
                return True, "Analyse fehlgeschlagen, Regex-PrÃ¼fung war okay."

        except Exception as e:
            logging.error(f"Fehler bei der Advanced Command Analysis: {e}")
            if getattr(settings, "fail_secure_llm_analysis", False):
                return False, f"Analyse fehlgeschlagen (LLM-Aufruf), Fail-Secure aktiv. Fehler: {e}"
            return True, "Analyse-Fehler"

    def close(self):
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                try:
                    self.process.kill()
                except (ProcessLookupError, PermissionError):
                    pass
            self.process = None


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
        except Exception as e:
            logging.error(f"Fehler beim Update der ShellPool-Metriken: {e}")

    def acquire(self, timeout: int = 10) -> PersistentShell:
        try:
            shell = self.pool.get(timeout=timeout)
            # Proaktive PrÃ¼fung der Shell-Gesundheit
            if not shell.is_healthy():
                logging.warning("Shell im Pool ist nicht gesund. Starte neu...")
                shell._start_process()
            self._update_metrics()
            return shell
        except Empty:
            logging.warning("Keine Shell im Pool verfÃ¼gbar. Erstelle temporÃ¤re Shell.")
            return PersistentShell(shell_cmd=self.shell_cmd)

    def release(self, shell: PersistentShell):
        if shell in self.shells:
            try:
                self.pool.put_nowait(shell)
            except Full:
                shell.close()
        else:
            # TemporÃ¤re Shell
            shell.close()
        self._update_metrics()

    def close_all(self):
        with self.lock:
            for shell in self.shells:
                shell.close()
            self.shells.clear()
            # Clear queue
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
