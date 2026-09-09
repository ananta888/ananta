"""Invalid private receive requests allocate no bridge/network resources."""

from unittest.mock import Mock

import pytest

from tests.meet_receive_infrastructure import private_receive


@pytest.mark.parametrize("source", [None, True, [], {}, "", "desktop", "Camera", "microphone;anything"])
def test_invalid_source_fails_before_bridge_or_build_access(tmp_path, monkeypatch, source):
    import tests.meet_companion_build
    import tests.meet_receive_infrastructure

    launch, build = Mock(), Mock()
    monkeypatch.setattr(tests.meet_receive_infrastructure.subprocess, "Popen", launch)
    monkeypatch.setattr(tests.meet_companion_build, "require_current_browser_build", build)
    with pytest.raises(ValueError, match="test_receive_source_invalid"):
        with private_receive(tmp_path, source=source):
            pytest.fail("invalid source was admitted")
    launch.assert_not_called()
    build.assert_not_called()
