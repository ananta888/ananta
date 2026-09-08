"""One fetch slot, fresh-result consumption, separate presentation and no fallback."""

from concurrent.futures import Future
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.test_browser_execution_lease import binding_fixture
from worker.meet_media.dialog_browser_screen import DialogBrowserScreen


class Pool:
    def __init__(self):
        self.calls = []
        self.shutdown = Mock()

    def submit(self, function, *args, **kwargs):
        future = Future()
        future.set_running_or_notify_cancel()
        self.calls.append((future, function, args, kwargs))
        return future


def setup():
    source, assignment, receipt, control = binding_fixture()
    clock = [0.0]
    page, browser, pool, pumps, workspaces, fetchers = Mock(), Mock(), Pool(), [], Mock(), Mock()
    page.url = assignment["meeting"]["origin"] + "/machine"

    def pump_factory(*args, **kwargs):
        pump = Mock(failed=False)
        pump.constructor_args, pump.constructor_kwargs = args, kwargs
        pumps.append(pump)
        return pump

    controller = DialogBrowserScreen(
        page,
        browser,
        assignment,
        pool=pool,
        pump_factory=pump_factory,
        workspace_factory=workspaces,
        fetch_factory=fetchers,
        clock=lambda: clock[0],
        wall_clock=lambda: 10 + clock[0],
    )
    return SimpleNamespace(**locals())


def update(f, source=None, control=None):
    f.controller.update(
        f.receipt, control or f.control, source or f.source, {"chat": False, "reply": False, "audio": "off"}
    )


def ready(f):
    update(f)
    f.pool.calls[0][0].set_result("<p>Public</p>")
    update(f)


def test_fetch_and_page_loading_never_run_in_tick_or_before_fresh_result_update():
    f = setup()
    update(f)
    assert len(f.pool.calls) == 1 and f.controller.workspace is None
    f.controller.tick()
    f.workspaces.assert_not_called()
    f.pool.calls[0][0].set_result("<p>Public</p>")
    f.controller.tick()
    f.workspaces.assert_not_called()
    update(f)
    f.workspaces.return_value.load.assert_called_once()
    assert len(f.pumps) == 2
    f.pumps[1].update.assert_called_once_with(f.control)
    assert f.pumps[0].update.call_count == 0
    f.controller.tick()
    f.pumps[1].tick.assert_called_once()


def test_navigation_before_presentation_fetches_once_without_opening_screen_source():
    f = setup()
    f.source["mode"] = "off"
    ready(f)
    assert len(f.pumps) == 1 and f.controller.workspace is not None
    selected = deepcopy(f.source) | {"revision": 3, "mode": "browser"}
    update(f, selected)
    assert len(f.pumps) == 2 and len(f.pool.calls) == 1


def test_screen_pause_resume_retains_page_and_uses_new_presentation_wrapper_without_refetch():
    f = setup()
    ready(f)
    workspace = f.controller.workspace
    paused = deepcopy(f.source)
    paused["binding"]["screen_revision"] = 2
    update(f, paused, f.control | {"enabled": False, "revision": 2})
    f.pumps[1].close.assert_called_once()
    workspace.close.assert_not_called()
    paused["binding"]["screen_revision"] = 3
    update(f, paused, f.control | {"revision": 3})
    assert f.controller.workspace is workspace and len(f.pool.calls) == 1 and len(f.pumps) == 3
    source = f.pumps[-1].constructor_kwargs["source_factory"](f.browser, f.assignment["session_id"])
    assert source.source_id == "screen:" + f.assignment["session_id"]
    assert source.generation.workspace_id == f.source["job"]["workspace_id"]
    source.close()
    workspace.close.assert_not_called()
    with pytest.raises(ValueError, match="presentation_closed"):
        source.take()


def test_rapid_navigation_cancels_old_lease_without_queuing_or_consuming_stale_html():
    f = setup()
    update(f)
    old_lease = f.controller.lease
    for revision in (3, 4, 5):
        changed = deepcopy(f.source)
        changed["revision"] = revision
        changed["job"]["task_id"] = "next-" + str(revision)
        changed["job"]["page_id"] = "page-" + str(revision)
        changed["job"]["navigation_revision"] = revision
        update(f, changed)
        assert len(f.pool.calls) == 1
    assert old_lease.closed and f.controller.lease is not old_lease
    f.pool.calls[0][0].set_result("<p>Obsolete</p>")
    update(f, changed)
    f.workspaces.assert_not_called()
    assert len(f.pool.calls) == 2
    f.pool.calls[1][0].set_result("<p>Current</p>")
    update(f, changed)
    assert f.workspaces.return_value.load.call_args.args[0] == "<p>Current</p>"


@pytest.mark.parametrize("failure", ["fetch", "load", "frame", "freshness"])
def test_source_failure_stops_only_browser_without_retry_or_status_fallback(failure):
    f = setup()
    update(f)
    if failure == "fetch":
        f.pool.calls[0][0].set_exception(ValueError("synthetic fetch failure"))
    else:
        f.pool.calls[0][0].set_result("<p>Public</p>")
    if failure == "load":
        f.workspaces.return_value.load.side_effect = ValueError("synthetic privacy denial")
    update(f)
    if failure == "frame":
        f.pumps[1].failed = True
    elif failure == "freshness":
        f.clock[0] = 2.5
    f.controller.tick()
    assert f.controller.failed and f.controller.workspace is None
    update(f)
    assert len(f.pool.calls) == 1
    f.pumps[0].update.assert_not_called()


def test_blocked_projection_clears_owned_workspace_and_never_reopens_old_task():
    f = setup()
    ready(f)
    blocked = deepcopy(f.source) | {"mode": "off", "job": None, "binding": None, "reason": "policy_denied"}
    update(f, blocked)
    assert f.controller.workspace is None
    f.workspaces.return_value.close.assert_called_once()
    update(f)
    assert len(f.pool.calls) == 1 and f.controller.workspace is None
    f.pumps[0].update.assert_not_called()


def test_only_explicit_status_selection_may_use_old_status_page():
    f = setup()
    ready(f)
    status = deepcopy(f.source) | {
        "revision": 3,
        "mode": "status",
        "job": None,
        "binding": None,
        "reason": "not_selected",
    }
    update(f, status)
    assert f.controller.workspace is None
    f.pumps[0].update.assert_called_once()


@pytest.mark.parametrize("case", ["page", "parent", "revision", "same_revision_new_job", "meet_generation"])
def test_changed_authority_never_publishes_or_fetches_replacement_implicitly(case):
    f = setup()
    ready(f)
    source = deepcopy(f.source)
    if case == "page":
        f.page.url = "https://other.example"
    elif case == "parent":
        source["job"]["parent_task_id"] = "other"
    elif case == "revision":
        source["revision"] = 1
    elif case == "same_revision_new_job":
        source["job"]["task_id"] = "other"
    else:
        f.receipt["lease"]["generation"] = source["binding"]["generation"] = 2
    if case == "meet_generation":
        update(f, source)  # Valid new receipt retires, but cannot resurrect old Task.
    else:
        with pytest.raises(ValueError, match="update_denied"):
            update(f, source)
    assert f.controller.workspace is None and len(f.pool.calls) == 1


def test_controller_close_is_idempotent_and_cancels_real_inflight_lease():
    f = setup()
    update(f)
    lease = f.controller.lease
    f.controller.close()
    f.controller.close()
    assert lease.closed and f.controller.closed
    f.pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
    with pytest.raises(ValueError, match="controller_closed"):
        update(f)
