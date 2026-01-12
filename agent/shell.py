import subprocess
import threading
import os
import time
import logging
import uuid
from queue import Queue, Empty

class PersistentShell:
    def __init__(self, shell_cmd: str = None):
        if shell_cmd is None:
            shell_cmd = "cmd.exe" if os.name == "nt" else "bash"
        
        self.shell_cmd = shell_cmd
        self.process = None
        self.lock = threading.Lock()
        self.output_queue = Queue()
        self.reader_thread = None
        self._start_process()

    def _start_process(self):
        if self.process:
            self.process.terminate()
        
        cmd = [self.shell_cmd]
        if os.name == "nt" and self.shell_cmd == "cmd.exe":
            cmd = [self.shell_cmd, "/q", "/k"]
        
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # Merge stderr into stdout for easier reading
            text=True,
            bufsize=1,
            shell=False
        )
        
        # Start reader thread
        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()
        
        # Initial wait to clear the welcome message of the shell
        if os.name == "nt":
            self.execute("echo off") # Reduce noise

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

    def close(self):
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()

_shell_instance = None
def get_shell() -> PersistentShell:
    global _shell_instance
    if _shell_instance is None:
        _shell_instance = PersistentShell()
    return _shell_instance
