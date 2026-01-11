import subprocess
import threading
import os
import time
import logging

class PersistentShell:
    def __init__(self, shell_cmd: str = None):
        if shell_cmd is None:
            shell_cmd = "cmd.exe" if os.name == "nt" else "bash"
        
        self.shell_cmd = shell_cmd
        self.process = None
        self.lock = threading.Lock()
        self._start_process()

    def _start_process(self):
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
        self.marker = f"---CMD_FINISHED_{time.time()}---"
        
        # Initial wait to clear the welcome message of the shell
        if os.name == "nt":
            self.execute("echo off") # Reduce noise

    def execute(self, command: str, timeout: int = 30) -> tuple[str, int | None]:
        with self.lock:
            if not self.process or self.process.poll() is not None:
                self._start_process()

            if os.name == "nt":
                full_command = f"{command}\necho {self.marker} %ERRORLEVEL%\n"
            else:
                full_command = f"{command}\necho {self.marker} $?\n"

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
                if time.time() - start_time > timeout:
                    return "".join(output) + "\n[Timeout]", -1
                
                line = self.process.stdout.readline()
                if not line:
                    if self.process.poll() is not None:
                        break
                    continue

                if self.marker in line:
                    try:
                        parts = line.strip().split(" ")
                        if len(parts) > 1:
                            exit_code = int(parts[-1])
                    except ValueError:
                        pass
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
