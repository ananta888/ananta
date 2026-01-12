import subprocess
import threading
import os
import time
import logging
import uuid
import re
from queue import Queue, Empty
from typing import List
try:
    from agent.config import settings
except ImportError:
    # Falls wir direkt im agent-Ordner sind
    from config import settings

class PersistentShell:
    def __init__(self, shell_cmd: str = None):
        if shell_cmd is None:
            shell_cmd = settings.shell_path
        
        if shell_cmd is None:
            shell_cmd = "cmd.exe" if os.name == "nt" else "bash"
        
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
        # Suche blacklist.txt im Hauptordner (ein Level über agent/) oder im aktuellen Arbeitsverzeichnis
        possible_paths = [
            os.path.join(os.getcwd(), "blacklist.txt"),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "blacklist.txt")
        ]
        for path in possible_paths:
            if os.path.exists(path):
                try:
                    mtime = os.path.getmtime(path)
                    if mtime > self.blacklist_mtime:
                        with open(path, "r") as f:
                            self.blacklist = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
                        self.blacklist_mtime = mtime
                        logging.info(f"Blacklist geladen ({len(self.blacklist)} Einträge) von {path}")
                    break
                except Exception as e:
                    logging.error(f"Fehler beim Laden der Blacklist von {path}: {e}")

    def _start_process(self):
        if self.process:
            self.process.terminate()
        
        cmd = [self.shell_cmd]

        if os.name == "nt":
            if self.shell_cmd == "cmd.exe":
                cmd = [self.shell_cmd, "/q", "/k"]
            elif self.is_powershell:
                cmd = [self.shell_cmd, "-NoLogo", "-NoExit", "-Command", "-"]
        
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            shell=False
        )
        
        # Start reader thread
        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()
        
        # Initial wait to clear the welcome message of the shell
        if os.name == "nt":
            if self.shell_cmd == "cmd.exe":
                self.execute("echo off") # Reduce noise
            elif self.is_powershell:
                self.execute("$ProgressPreference = 'SilentlyContinue'") # Reduce noise

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
        # Blacklist-Prüfung mittels Regex
        self._load_blacklist()
        for pattern in self.blacklist:
            try:
                if re.search(pattern, command, re.IGNORECASE):
                    logging.warning(f"Gefährlicher Befehl blockiert: {command} (Match mit Pattern '{pattern}')")
                    return f"Error: Command matches blacklisted pattern '{pattern}'", -1
            except re.error as e:
                # Falls das Pattern kein gültiges Regex ist, nutzen wir einfachen Substring-Match
                if pattern in command:
                    logging.warning(f"Gefährlicher Befehl blockiert: {command} (enthält '{pattern}')")
                    return f"Error: Command contains blacklisted pattern '{pattern}'", -1
                logging.error(f"Ungültiges Regex-Pattern in Blacklist: {pattern} ({e})")

        # Advanced Command Analysis mittels LLM (optional)
        if settings.enable_advanced_command_analysis:
            is_safe, reason = self._analyze_command_intent(command)
            if not is_safe:
                logging.warning(f"Befehl durch LLM-Analyse blockiert: {command}. Grund: {reason}")
                return f"Error: Command blocked by LLM analysis. Reason: {reason}", -1

        # Analyse potenziell gefährlicher Parameter
        dangerous_params = [";", "&&", "||", "|", ">", ">>", "<", "`", "$("]
        # In PowerShell sind auch andere gefährlich, aber das deckt schon viel ab.
        # Wir loggen nur Warnungen für diese, blockieren aber nicht alles, 
        # da Pipes oft gewollt sind.
        if any(p in command for p in dangerous_params):
            logging.info(f"Kommando enthält Shell-Metazeichen: {command}")

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
                    full_command = f"$Error.Clear(); {command}; $lsc=$LASTEXITCODE; if($Error.Count -gt 0 -and ($null -eq $lsc -or $lsc -eq 0)){{$lsc=1}}; echo \"{current_marker} $lsc\"\n"
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
                    return "".join(output) + "\n[Timeout]", -1
                
                try:
                    line = self.output_queue.get(timeout=max(0.1, timeout - elapsed))
                except Empty:
                    if self.process.poll() is not None:
                        break
                    continue

                if current_marker in line:
                    try:
                        parts = line.strip().split(" ")
                        if len(parts) > 1:
                            exit_code = int(parts[-1])
                    except ValueError as e:
                        logging.warning(f"Konnte Exit-Code nicht parsen: {e}")
                    break
                output.append(line)
            
            return "".join(output).strip(), exit_code

    def _analyze_command_intent(self, command: str) -> tuple[bool, str]:
        """Nutzt ein LLM, um die Intention eines Befehls zu analysieren."""
        try:
            from agent.llm_integration import _call_llm
            import json
            
            prompt = (
                f"Analysiere den folgenden Shell-Befehl auf bösartige Absichten oder extreme Gefährlichkeit "
                f"(z.B. Löschen des gesamten Systems, Ändern von Admin-Passwörtern, Exfiltration sensibler Daten):\n\n"
                f"Befehl: {command}\n\n"
                f"Antworte NUR in folgendem JSON-Format:\n"
                f"{{\n"
                f"  \"safe\": true/false,\n"
                f"  \"reason\": \"Begründung hier\"\n"
                f"}}"
            )
            
            # Wir nutzen die Default-Einstellungen für die Analyse
            urls = {
                "ollama": settings.ollama_url,
                "lmstudio": settings.lmstudio_url,
                "openai": settings.openai_url,
                "anthropic": settings.anthropic_url
            }
            
            res_raw = _call_llm(
                provider=settings.default_provider,
                model=settings.default_model,
                prompt=prompt,
                urls=urls,
                api_key=settings.openai_api_key
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
                
                return safe, data.get("reason", "Keine Begründung angegeben")
            except Exception as e:
                logging.error(f"Fehler beim Parsen der LLM-Analyse: {e}. Raw: {res_raw}")
                # Falls die Analyse fehlschlägt, entscheiden wir basierend auf Fail-Secure
                if getattr(settings, 'fail_secure_llm_analysis', False):
                    return False, f"Analyse fehlgeschlagen (Parser-Fehler), Fail-Secure aktiv. Fehler: {e}"
                return True, "Analyse fehlgeschlagen, Regex-Prüfung war okay."
                
        except Exception as e:
            logging.error(f"Fehler bei der Advanced Command Analysis: {e}")
            if getattr(settings, 'fail_secure_llm_analysis', False):
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
                except:
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
        logging.info(f"ShellPool mit {size} Instanzen initialisiert.")

    def acquire(self, timeout: int = 10) -> PersistentShell:
        try:
            return self.pool.get(timeout=timeout)
        except Empty:
            logging.warning("Keine Shell im Pool verfügbar. Erstelle temporäre Shell.")
            return PersistentShell(shell_cmd=self.shell_cmd)

    def release(self, shell: PersistentShell):
        if shell in self.shells:
            try:
                self.pool.put_nowait(shell)
            except:
                shell.close()
        else:
            # Temporäre Shell
            shell.close()

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
