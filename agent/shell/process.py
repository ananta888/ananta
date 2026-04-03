from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
import uuid
from queue import Empty, Queue

from . import security
from .runtime import settings


class PersistentShell:
    def __init__(self, shell_cmd: str = None):
        if shell_cmd is None:
            shell_cmd = settings.shell_path
        if shell_cmd is None:
            if os.name == "nt":
                shell_cmd = "cmd.exe"
            else:
                import shutil

                shell_cmd = "bash" if shutil.which("bash") else "sh"
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
        patterns, mtime = security.load_blacklist(self.blacklist_mtime)
        if patterns:
            self.blacklist = patterns
        self.blacklist_mtime = mtime

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
            shell_name = os.path.basename(self.shell_cmd).lower()
            cmd = [self.shell_cmd, "--noprofile", "--norc"] if shell_name == "bash" else [self.shell_cmd]
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
                env=os.environ.copy(),
            )
        except Exception as exc:
            logging.error(f"Konnte Shell-Prozess '{self.shell_cmd}' nicht starten: {exc}")
            if self.shell_cmd != "sh" and os.name != "nt":
                logging.info("Versuche Fallback auf /bin/sh")
                self.shell_cmd = "sh"
                return self._start_process()
            raise
        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()
        if os.name == "nt":
            if self.shell_cmd == "cmd.exe":
                self.execute("echo off")
            elif self.is_powershell:
                self.execute("$ProgressPreference = 'SilentlyContinue'")

    def _read_output(self):
        while self.process and self.process.stdout:
            try:
                line = self.process.stdout.readline()
            except Exception as exc:
                logging.warning(f"Shell output reader stopped unexpectedly: {exc}")
                break
            if line:
                self.output_queue.put(line)
            else:
                if self.process.poll() is not None:
                    break
                time.sleep(0.1)

    def _validate_tokens(self, command: str) -> tuple[bool, str]:
        return security.validate_tokens(command, blacklist=self.blacklist, is_powershell=self.is_powershell)

    def _validate_meta_characters(self, command: str) -> tuple[bool, str]:
        return security.validate_meta_characters(command)

    def _analyze_command_intent(self, command: str) -> tuple[bool, str]:
        return security.analyze_command_intent(command)

    def execute(self, command: str, timeout: int = 30) -> tuple[str, int | None]:
        self._load_blacklist()
        is_allowed, reason = security.validate_blacklist_patterns(command, self.blacklist)
        if not is_allowed:
            logging.warning(f"Gefaehrlicher Befehl blockiert: {command} ({reason})")
            return f"Error: {reason}", -1
        is_safe_tokens, reason_tokens = self._validate_tokens(command)
        if not is_safe_tokens:
            logging.warning(f"Befehl durch Token-Pruefung blockiert: {command}. Grund: {reason_tokens}")
            return f"Error: {reason_tokens}", -1
        is_safe_meta, reason_meta = self._validate_meta_characters(command)
        if not is_safe_meta:
            logging.warning(f"Befehl durch Metazeichen-Pruefung blockiert: {command}. Grund: {reason_meta}")
            return f"Error: {reason_meta}", -1
        if settings.enable_advanced_command_analysis:
            is_safe, reason = self._analyze_command_intent(command)
            if not is_safe:
                logging.warning(f"Befehl durch LLM-Analyse blockiert: {command}. Grund: {reason}")
                return f"Error: Command blocked by LLM analysis. Reason: {reason}", -1

        with self.lock:
            if not self.process or self.process.poll() is not None:
                self._start_process()
            while not self.output_queue.empty():
                try:
                    self.output_queue.get_nowait()
                except Empty:
                    break
            marker = f"---CMD_FINISHED_{uuid.uuid4()}---"
            if os.name == "nt":
                if self.is_powershell:
                    full_command = (
                        f"$Error.Clear(); {command}; "
                        "$lsc = if($?) { 0 } else { 1 }; "
                        "if($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { $lsc = $LASTEXITCODE }; "
                        "if($Error.Count -gt 0 -and $lsc -eq 0) { $lsc = 1 }; "
                        f'echo "{marker} $lsc"\n'
                    )
                else:
                    full_command = f"{command}\necho {marker} %ERRORLEVEL%\n"
            else:
                full_command = f"{command}\necho {marker} $?\n"
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
                    logging.warning(f"Timeout bei Befehlsausfuehrung: {command}")
                    return "".join(output) + "\n[Error: Timeout]", -1
                try:
                    line = self.output_queue.get(timeout=max(0.1, timeout - elapsed))
                except Empty:
                    if self.process.poll() is not None:
                        logging.error(f"Shell-Prozess unerwartet beendet waehrend: {command}")
                        return "".join(output) + "\n[Error: Shell process terminated unexpectedly]", -1
                    continue
                if marker in line:
                    try:
                        parts = line.strip().split(" ")
                        if len(parts) > 1:
                            exit_code = int(parts[-1])
                    except (ValueError, IndexError) as exc:
                        logging.warning(f"Konnte Exit-Code nicht parsen: {exc}")
                    break
                output.append(line)
            return "".join(output).strip(), exit_code

    def is_healthy(self) -> bool:
        with self.lock:
            return bool(self.process and self.process.poll() is None)

    def close(self):
        if self.process:
            try:
                if self.process.stdin:
                    self.process.stdin.close()
            except Exception:
                pass
            try:
                if self.process.stdout:
                    self.process.stdout.close()
            except Exception:
                pass
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                try:
                    self.process.kill()
                except (ProcessLookupError, PermissionError):
                    pass
            self.process = None
        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=0.5)
        self.reader_thread = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
