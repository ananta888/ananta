"""WSL projection preserves the private GPU probe's isolation contract."""

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.meet.test_meet_gpu_source_fixture import IMAGE, NAME
from tests.meet_gpu_source_fixture import probe_command, run_probe
from tests.meet_gpu_wsl_fixture import LIBRARIES, LIBRARY_ROOT, STORE_FILES, driver_bindings

STORE = "/usr/lib/wsl/drivers/nv_dispig.inf_amd64_" + "a" * 16


def test_wsl_projection_uses_only_fixed_read_only_libraries_and_dxg(tmp_path):
    bindings = driver_bindings(STORE, is_file=lambda _: True, is_device=lambda _: True)
    assert len(bindings) == len(LIBRARIES) + len(STORE_FILES)
    assert all(value.startswith((f"type=bind,src={LIBRARY_ROOT}/", f"type=bind,src={STORE}/"))
               and value.endswith(",readonly")
               for value in bindings)
    args = probe_command(NAME, IMAGE, bindings, "speech_smoke", tmp_path,
                         packaged=True, host_profile="wsl", driver_store=STORE)
    assert "--env=LD_LIBRARY_PATH=/usr/lib/wsl/lib:" + STORE in args
    assert [args[i + 1] for i, value in enumerate(args) if value == "--device"] == ["/dev/dxg"]
    assert all(value in args for value in ("--network=none", "--read-only", "--cap-drop=ALL",
                                          "--security-opt=no-new-privileges"))
    assert not any(value in " ".join(args) for value in ("/state", "docker.sock", "/run/secrets", "dst=/app/"))


@pytest.mark.parametrize("missing", ["device", *sorted(set(LIBRARIES + STORE_FILES))])
def test_missing_wsl_dependency_fails_closed(missing):
    with pytest.raises(ValueError, match="test_gpu_wsl_driver_binding_missing"):
        driver_bindings(STORE, is_file=lambda path: path.name != missing,
                        is_device=lambda path: path == Path("/dev/dxg") and missing != "device")


@pytest.mark.parametrize("profile", ["", "auto", "../wsl", True])
def test_unknown_profile_never_reads_service_or_creates_container(profile):
    command = Mock()
    with pytest.raises(ValueError, match="test_gpu_host_profile_invalid"):
        run_probe("speech_smoke", command=command, host_profile=profile)
    command.assert_not_called()


@pytest.mark.parametrize("store", [None, "", "/private", STORE + "/..", STORE.replace("a", "g"), True])
def test_invalid_store_never_reads_service_or_creates_container(store):
    command = Mock()
    with pytest.raises(ValueError, match="test_gpu_wsl_driver_store_invalid"):
        run_probe("speech_smoke", command=command, host_profile="wsl", driver_store=store)
    command.assert_not_called()


def test_native_profile_does_not_silently_ignore_a_wsl_store():
    command = Mock()
    with pytest.raises(ValueError, match="test_gpu_wsl_driver_store_invalid"):
        run_probe("speech_smoke", command=command, driver_store=STORE)
    command.assert_not_called()


@pytest.mark.skipif(os.environ.get("MEET_WSL_PACKAGED_GPU_GATE") != "1",
                    reason="explicit isolated WSL packaged GPU probe")
@pytest.mark.timeout(150)
@pytest.mark.parametrize("module", ["speech_smoke", "persona_visual_smoke"])
def test_actual_wsl_packaged_gpu_execution(module):
    report = run_probe(module, packaged_image=os.environ["MEET_COMPANION_PACKAGED_IMAGE"],
                       host_profile="wsl", driver_store=os.environ["MEET_TEST_WSL_DRIVER_STORE"])
    assert report["classification"] == "synthetic_local_technical_observation"
    assert report["production_release_evidence"] is False
    assert report["engine" if module == "speech_smoke" else "renderer"] == (
        "piper-cuda" if module == "speech_smoke" else "persona-clip-h264_nvenc"
    )
