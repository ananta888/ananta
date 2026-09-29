"""Test-only explicit local tool prefix; production keeps /usr/bin codec paths."""

import os
import sys
from pathlib import Path

from voice_runtime.preprocessing.audio_decode import BoundedSubprocessRunner


class NativePersonaCodecRunner:
    def __init__(self):
        configured = os.environ.get("PERSONA_GENERATION_NATIVE_TOOLS_PREFIX")
        self.paths = {}
        libraries = ()
        if configured:
            prefix = Path(configured)
            if not prefix.is_absolute() or not prefix.is_dir():
                raise ValueError("test_native_tool_prefix_invalid")
            libraries = (str(prefix / "usr/lib/x86_64-linux-gnu"),)
            self.paths = {f"/usr/bin/{name}": str(prefix / "usr/bin" / name) for name in ("ffmpeg", "ffprobe")}
        for name in ("ffmpeg", "ffprobe"):
            path = self.paths.get(f"/usr/bin/{name}", f"/usr/bin/{name}")
            if not Path(path).is_file():
                raise ValueError("test_native_codec_not_installed")
        self.runner = BoundedSubprocessRunner(library_paths=libraries)

    def run(self, command, **kwargs):
        if command[0] not in (sys.executable, "/usr/bin/ffmpeg", "/usr/bin/ffprobe"):
            raise ValueError("test_native_command_not_admitted")
        return self.runner.run([self.paths.get(command[0], command[0]), *command[1:]], **kwargs)
