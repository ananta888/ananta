"""Pure output framing: chunk boundaries never become event or secret boundaries."""

import io
import queue
from threading import Event

import pytest

from agent.cli_backends.coding_agent_process_io import PIPE_CHARS, PIPE_FAILURE, pump_lines
from agent.cli_backends.coding_agent_process_output import ProcessOutput


def test_chunks_preserve_complete_json_lines_and_split_secret_redaction():
    events = []
    output = ProcessOutput(20_000, events.append, ("secret-value",))
    first = '{"text":"' + "x" * 5000 + "secret-"
    assert output.feed("stdout", first)
    assert events == []
    assert output.feed("stderr", "diagnostic\n")
    assert output.feed("stdout", 'value\u2028still-json"}\nlast')
    assert output.feed("stdout", None)
    assert [event.sequence for event in events] == [1, 2, 3]
    assert [event.stream for event in events] == ["stderr", "stdout", "stdout"]
    assert events[1].text.endswith('<redacted>\u2028still-json"}')
    assert events[2].text == "last"
    assert "secret-value" not in output.text("stdout")


def test_raw_unterminated_input_and_redaction_expansion_are_both_bounded():
    output = ProcessOutput(12, None, ("abcd",))
    assert output.feed("stdout", "abcdabcd")
    assert not output.feed("stdout", "\n")  # Raw fits; replacement would overflow.
    assert output.text("stdout") == ""
    assert not output.feed("stdout", None)
    raw = ProcessOutput(12, None, ())
    assert raw.feed("stdout", "x" * 12)
    assert not raw.feed("stderr", "y")  # One total budget, not one per stream.
    assert raw.text("stdout") == ""


def test_event_sink_failure_does_not_change_collected_output():
    def broken(event):
        raise RuntimeError("observer failed")

    output = ProcessOutput(100, broken, ())
    assert output.feed("stdout", "one\ntwo\n")
    assert output.feed("stdout", None)
    assert output.text("stdout") == "one\ntwo\n"


@pytest.mark.parametrize("failure", ["read", "close"])
def test_pipe_failure_is_reported_with_terminal_marker(failure):
    class Broken(io.StringIO):
        def readline(self, limit):
            assert limit == PIPE_CHARS
            if failure == "read":
                raise OSError("read failed")
            return ""

        def close(self):
            if failure == "close":
                raise OSError("close failed")
            super().close()

    messages = queue.Queue()
    pump_lines("stdout", Broken(), messages, Event())
    assert messages.get_nowait() == ("stdout", PIPE_FAILURE)
    assert messages.get_nowait() == ("stdout", None)


def test_reader_never_requests_or_queues_an_unbounded_line():
    messages = queue.Queue()
    pump_lines("stdout", io.StringIO("x" * (PIPE_CHARS * 2 + 1)), messages, Event())
    assert [len(messages.get_nowait()[1]) for _ in range(3)] == [PIPE_CHARS, PIPE_CHARS, 1]
    assert messages.get_nowait() == ("stdout", None)
