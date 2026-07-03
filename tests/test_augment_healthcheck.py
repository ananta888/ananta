from unittest.mock import patch
from agent.services.augment.augment_healthcheck import AugmentHealthcheck


def test_no_auggie_binary_unavailable():
    hc = AugmentHealthcheck()
    with patch("shutil.which", return_value=None):
        status = hc.run()
    assert status.overall == "unavailable"
    assert not status.is_ready_for_cli_worker()


def test_check_node_not_found():
    hc = AugmentHealthcheck()
    with patch("shutil.which", return_value=None):
        result = hc._check_node()
    assert result.passed is False


def test_run_returns_structured_status():
    hc = AugmentHealthcheck()
    with patch("shutil.which", return_value=None):
        status = hc.run()
    assert hasattr(status, "overall")
    assert isinstance(status.checks, list)


def test_is_ready_for_cli_worker_false_without_binary():
    hc = AugmentHealthcheck()
    with patch("shutil.which", return_value=None):
        status = hc.run()
    assert status.is_ready_for_cli_worker() is False


def test_timeout_does_not_crash():
    import subprocess
    hc = AugmentHealthcheck()
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("auggie", 5)):
        with patch("shutil.which", return_value="/usr/bin/auggie"):
            result = hc._check_auggie_version()
    assert result.passed is False


def test_is_ready_for_context_provider_false_without_binary():
    hc = AugmentHealthcheck()
    with patch("shutil.which", return_value=None):
        status = hc.run()
    assert status.is_ready_for_context_provider() is False


def test_checked_at_is_float():
    hc = AugmentHealthcheck()
    with patch("shutil.which", return_value=None):
        status = hc.run()
    assert isinstance(status.checked_at, float)
    assert status.checked_at > 0


def test_checks_list_has_at_least_two_entries():
    hc = AugmentHealthcheck()
    with patch("shutil.which", return_value=None):
        status = hc.run()
    # node check + auggie_binary check always run
    assert len(status.checks) >= 2


def test_check_auggie_binary_not_found():
    hc = AugmentHealthcheck()
    with patch("shutil.which", return_value=None):
        result = hc._check_auggie_binary()
    assert result.passed is False
    assert "not found" in result.message


def test_check_auggie_version_timeout():
    import subprocess
    hc = AugmentHealthcheck()
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("auggie", 5)):
        result = hc._check_auggie_version()
    assert result.passed is False
    assert "Timeout" in result.message
