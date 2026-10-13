"""Actual killed children cannot strand WAV/media files outside their Task owner."""

import json
import os
import sys
from pathlib import Path

import pytest

from worker.meet_media.companion_execution_process import CompanionExecutionProcess
from worker.meet_media.companion_media_process import CompanionMediaPreparationProcess
from worker.meet_media.companion_task_controls import CompanionSourceInterrupted
from worker.meet_media.companion_task_scratch import TaskExecutionScratch


@pytest.mark.timeout(10)
@pytest.mark.parametrize("kind", ["speech", "media"])
def test_killed_child_leaves_only_parent_owned_scratch_then_task_close_removes_it(kind):
    scratch = TaskExecutionScratch()
    root = Path(scratch.environment["CUDA_CACHE_PATH"])
    temporary = Path(scratch.environment["TMPDIR"])
    marker = temporary / "child-observation.json"
    code = '''
import json,sys,tempfile,time
from pathlib import Path
sys.stdin.readline()
with tempfile.TemporaryDirectory(prefix="synthetic-wave-") as directory:
 target=Path(directory)/"speech.wav"
 target.write_bytes(b"synthetic-private-audio")
 (Path(tempfile.gettempdir())/"child-observation.json").write_text(json.dumps(str(target)))
 time.sleep(30)
'''
    options = {"command": (sys.executable, "-u", "-c", code), "environment": os.environ | scratch.environment}
    process = CompanionExecutionProcess(**options) if kind == "speech" else CompanionMediaPreparationProcess(**options)
    def interrupt():
        if marker.exists():
            raise CompanionSourceInterrupted("synthetic_speech_retired")
    try:
        with pytest.raises(CompanionSourceInterrupted):
            if kind == "speech":
                process.call("speech", "synthetic", cooperate=interrupt)
            else:
                process.idle("idle", cooperate=interrupt)
        wav = Path(json.loads(marker.read_text()))
        assert process._transport.process.poll() is not None
        assert wav.is_relative_to(temporary) and wav.read_bytes() == b"synthetic-private-audio"
        assert temporary.stat().st_mode & 0o777 == 0o700
    finally:
        try:
            process.close()
        finally:
            scratch.close()
    assert not root.exists()


def test_scratch_cleanup_never_removes_another_task_or_parent_tmpdir(tmp_path):
    parent = tmp_path / "parent.wav"
    parent.write_bytes(b"synthetic-parent")
    first, second = TaskExecutionScratch(), TaskExecutionScratch()
    try:
        survivor = Path(second.environment["TMPDIR"]) / "other.wav"
        survivor.write_bytes(b"synthetic-other-task")
        (Path(first.environment["TMPDIR"]) / "external-directory").symlink_to(tmp_path, target_is_directory=True)
        first.close()
        assert survivor.read_bytes() == b"synthetic-other-task"
        assert parent.read_bytes() == b"synthetic-parent"
        first.close()
    finally:
        first.close()
        second.close()
