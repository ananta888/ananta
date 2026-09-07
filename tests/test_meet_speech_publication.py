"""Deterministic sink tests: browser policy and Hub issuance are not substituted."""

import base64
from unittest.mock import Mock

import pytest

from ananta_contracts.meet_speech_source import source_receipt
from worker.meet_media.audio_output import SpeechFrame
from worker.meet_media.speech_browser import BrowserSpeechPort
from worker.meet_media.speech_publication import SpeechPublication

NOW = 1788800000


class Browser:
    def __init__(self):
        self.generation = 1
        self.sent = self.played = 0
        self.closed = []
        self.frames = []
        self.transform_receipt = lambda value: value
        self.transform_status = lambda value: value

    def open(self, source_id, total_samples):
        return self.transform_receipt(
            {
                "schema": "ananta.meet-speech-source.v1",
                "sourceId": source_id,
                "generation": self.generation,
                "sampleRate": 22050,
                "channels": 1,
                "format": "pcm_s16le",
                "totalSamples": total_samples,
                "queueSamples": 4410,
                "expiresAt": (NOW + 50) * 1000,
            }
        )

    def status(self):
        return self.transform_status(
            {
                "state": "open",
                "generation": self.generation,
                "receivedSamples": self.sent,
                "playedSamples": self.played,
                "bufferedSamples": self.sent - self.played,
            }
        )

    def push(self, generation, start_sample, pcm_base64):
        assert generation == self.generation and start_sample == self.sent
        self.frames.append((start_sample, base64.b64decode(pcm_base64, validate=True)))
        self.sent += len(self.frames[-1][1]) // 2

    def close(self, generation):
        if generation == self.generation:
            self.closed.append(generation)


def sink(browser=None, samples=66150, checkpoint=None, wall=None, monotonic=None):
    return SpeechPublication(
        browser or Browser(),
        "hub-session",
        samples,
        checkpoint or (lambda: None),
        clock=wall or (lambda: NOW),
        monotonic=monotonic or (lambda: 100),
    )


def frame(start, count=441):
    return SpeechFrame(start, b"\x01\x02" * count)


def test_exact_pcm_sample_clock_backpressure_and_partial_final_frame():
    browser = Browser()
    output = sink(browser, samples=4859)
    for index in range(10):
        assert output.push(frame(index * 441)) is True
    pending = frame(4410)
    assert output.writable_samples() == 0
    assert output.push(pending) is False
    assert len(browser.frames) == 10
    browser.played = 441
    assert output.push(pending) is True
    browser.played = 4851
    assert output.push(frame(4851, 8)) is True
    assert browser.frames[-1] == (4851, b"\x01\x02" * 8)
    browser.transform_status = lambda _: {
        "state": "completed",
        "generation": 2,
        "receivedSamples": 4859,
        "playedSamples": 4859,
        "bufferedSamples": 0,
    }
    assert output.writable_samples() == 0 and output.completed is True
    output.close()


@pytest.mark.parametrize(
    "change",
    [
        {"generation": True},
        {"generation": 0},
        {"generation": 4097},
        {"sourceId": "speech:foreign"},
        {"sampleRate": 48000},
        {"channels": True},
        {"format": "float32"},
        {"totalSamples": 42},
        {"queueSamples": 8820},
        {"expiresAt": NOW * 1000},
        {"expiresAt": (NOW + 51) * 1000},
        {"unknown": True},
    ],
)
def test_closed_browser_receipt_rejects_mutation_before_pcm(change):
    browser = Browser()
    browser.transform_receipt = lambda value: value | change
    with pytest.raises(ValueError, match="receipt_invalid"):
        sink(browser)
    assert browser.frames == []


@pytest.mark.parametrize(
    "change",
    [
        {"state": "failed"},
        {"state": "closed"},
        {"state": "starting"},
        {"generation": 2},
        {"receivedSamples": 442},
        {"playedSamples": -1},
        {"playedSamples": 442},
        {"bufferedSamples": 0},
        {"bufferedSamples": True},
        {"unknown": 0},
        {"state": "completed", "generation": 2},
    ],
)
def test_inconsistent_stale_or_closed_progress_stops_own_generation(change):
    browser = Browser()
    output = sink(browser)
    output.push(frame(0))
    browser.transform_status = lambda value: value | change
    with pytest.raises(ValueError, match="progress_invalid"):
        output.writable_samples()
    assert browser.closed == [1]
    output.close()
    assert browser.closed == [1]


def test_regressing_progress_and_new_source_never_reopen_or_stop_the_new_generation():
    browser = Browser()
    output = sink(browser)
    output.push(frame(0))
    browser.played = 100
    output.writable_samples()
    browser.played = 50
    with pytest.raises(ValueError):
        output.writable_samples()
    fresh = Browser()
    output = sink(fresh)
    fresh.generation = 3
    with pytest.raises(ValueError):
        output.writable_samples()
    assert fresh.closed == []


@pytest.mark.parametrize(
    "bad_frame",
    [
        frame(1),
        frame(0, 440),
        frame(0, 442),
        SpeechFrame(True, b"\0" * 882),
        SpeechFrame(0, bytearray(882)),
        SpeechFrame(0, b""),
        None,
    ],
)
def test_malformed_order_or_frame_size_closes_without_partial_write(bad_frame):
    browser = Browser()
    output = sink(browser)
    with pytest.raises(ValueError, match="frame_invalid"):
        output.push(bad_frame)
    assert browser.frames == [] and browser.closed == [1]


def test_authority_is_rechecked_after_status_and_after_write():
    for operation in ("status", "push"):
        browser = Browser()
        current = True

        def checkpoint():
            if not current:
                raise PermissionError("test_authority_revoked")

        original = getattr(browser, operation)

        def revoke(*args):
            nonlocal current
            value = original(*args)
            current = False
            return value

        output = sink(browser, checkpoint=checkpoint)
        setattr(browser, operation, revoke)
        with pytest.raises(PermissionError):
            output.push(frame(0))
        assert browser.closed == [1]
        assert len(browser.frames) == (0 if operation == "status" else 1)


@pytest.mark.parametrize("change", ["rollback", "lease", "monotonic"])
def test_clock_and_local_deadline_fail_bounded(change):
    wall, steady = NOW, 100
    browser = Browser()
    output = sink(browser, wall=lambda: wall, monotonic=lambda: steady)
    if change == "rollback":
        wall -= 1
    elif change == "lease":
        wall += 50
    else:
        steady += 50
    with pytest.raises(ValueError, match="expired"):
        output.writable_samples()
    assert browser.closed == [1]


def test_revocation_during_open_and_ambiguous_write_release_only_owned_generation():
    browser = Browser()
    checkpoint = Mock(side_effect=[None, PermissionError("test_revoked")])
    with pytest.raises(PermissionError):
        sink(browser, checkpoint=checkpoint)
    assert browser.closed == [1]
    browser = Browser()
    output = sink(browser)
    browser.push = Mock(side_effect=OSError("test_transport_failed"))
    with pytest.raises(OSError):
        output.push(frame(0))
    assert browser.closed == [1]


def test_browser_adapter_passes_no_urls_or_grants_and_cleanup_is_generation_conditional():
    page = Mock()
    page.evaluate.return_value = {"state": "done", "result": {"test": "receipt"}}
    browser = BrowserSpeechPort(page, lambda: None)
    browser.open("speech:hub-session", 441)
    assert page.evaluate.call_args_list[0].args[1][1:] == ["speech:hub-session", 441]
    browser.push(1, 0, "AQI=")
    assert page.evaluate.call_args.args[1] == [1, 0, "AQI="]
    browser.close(1)
    assert "source.status().generation === gen" in page.evaluate.call_args.args[0]
    assert page.evaluate.call_args.args[1] == 1


@pytest.mark.parametrize("samples", [0, -1, True, 882001, 1.5])
def test_invalid_budgets_never_open_browser(samples):
    browser = Mock()
    with pytest.raises(ValueError):
        sink(browser, samples=samples)
    browser.open.assert_not_called()


def test_receipt_is_immutable_and_does_not_carry_media_or_authorization():
    value = Browser().open("speech:hub-session", 441)
    receipt = source_receipt(value, source_id="speech:hub-session", total_samples=441, now_ms=NOW * 1000)
    with pytest.raises(AttributeError):
        receipt.generation = 9
    assert set(vars(receipt)) == {"source_id", "generation", "total_samples", "expires_at_ms"}
