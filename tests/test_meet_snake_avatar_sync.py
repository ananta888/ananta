"""Snake avatar: shared audio/video timeline, lip-sync, state machine and drift."""

import hashlib

import numpy as np
import pytest

from worker.meet_media.companion_media import (
    FRAME_SAMPLES,
    PUSH_LEAD_SECONDS,
    SPEECH_RATE,
    AvatarPorts,
    IdleClips,
    SpeechAvatarPublisher,
)
from worker.meet_media.snake_avatar import build_video, render_pose, snake_frame
from worker.meet_media.snake_avatar_state import (
    BODY_LANGUAGE,
    IDLE,
    LISTENING,
    SPEAKING,
    STATES,
    THINKING,
    TRANSITION_SECONDS,
    AvatarStateMachine,
)
from worker.meet_media.snake_avatar_timeline import (
    FPS,
    MAX_CLIP_FRAMES,
    MAX_FRAME_DRIFT_US,
    MICROSECONDS,
    AnimationController,
    BlinkSchedule,
    SpeechMediaTimeline,
)

pytestmark = pytest.mark.timeout(120)
RATE = SPEECH_RATE


def tone(seconds, *, amplitude=0.4, hz=220.0):
    t = np.arange(int(seconds * RATE)) / RATE
    return (amplitude * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def speech_with_pauses(pattern):
    """``pattern`` is a list of (seconds, voiced) tuples on one timeline."""
    parts = [tone(seconds) if voiced else np.zeros(int(seconds * RATE), dtype=np.float32) for seconds, voiced in pattern]
    return np.concatenate(parts)


def pcm_bytes(samples):
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def fake_encoder(raw_path, video_path, frames):
    """Stands in for FFmpeg: emits a minimal ftyp-prefixed container."""
    raw = raw_path.read_bytes()
    assert len(raw) == frames * 256 * 256 * 3
    video_path.write_bytes(b"\x00\x00\x00\x18ftypisom" + frames.to_bytes(4, "big") + hashlib.sha256(raw).digest())


# --- timeline -------------------------------------------------------------


def test_frames_are_projections_of_the_audio_sample_clock():
    timeline = SpeechMediaTimeline(tone(3.7), RATE)
    assert timeline.frames == -(-timeline.total_samples * FPS // RATE)
    for index in range(timeline.frames):
        start, end = timeline.frame_window(index)
        assert timeline.frame_time_us(index) == timeline.audio_time_us(start)
        assert abs(timeline.frame_time_us(index) - index * MICROSECONDS // FPS) <= MICROSECONDS // RATE + 1
        assert end - start <= RATE // FPS + 1


def test_mouth_opens_on_voiced_audio_and_closes_reliably_in_pauses():
    samples = speech_with_pauses([(1.0, True), (0.6, False), (1.0, True), (0.8, False)])
    timeline = SpeechMediaTimeline(samples, RATE)
    envelope = timeline.envelope()
    voiced_a = envelope[2 : int(1.0 * FPS) - 1]
    pause_a = envelope[int(1.3 * FPS) : int(1.6 * FPS)]
    voiced_b = envelope[int(1.7 * FPS) : int(2.6 * FPS) - 1]
    pause_b = envelope[int(2.9 * FPS) :]
    assert min(voiced_a) > 0.3 and min(voiced_b) > 0.3
    assert max(pause_a) == 0.0 and max(pause_b) == 0.0
    # Release closes within a few frames after the audio stops (no lag).
    assert envelope[int(1.0 * FPS) + 3] < 0.2


def test_long_answer_splits_into_contract_sized_segments_without_gaps():
    timeline = SpeechMediaTimeline(tone(38.0), RATE)
    segments = timeline.segments()
    assert len(segments) == -(-timeline.frames // MAX_CLIP_FRAMES)
    assert segments[0].start_sample == 0 and segments[-1].end_sample == timeline.total_samples
    for previous, current in zip(segments, segments[1:]):
        assert previous.end_sample == current.start_sample
        assert previous.first_frame + previous.frames == current.first_frame
    assert all(2 <= segment.frames <= MAX_CLIP_FRAMES for segment in segments)


def test_one_frame_tail_is_folded_into_the_previous_segment():
    samples = np.zeros(MAX_CLIP_FRAMES * RATE // FPS + RATE // FPS // 2, dtype=np.float32)
    segments = SpeechMediaTimeline(samples, RATE).segments()
    assert len(segments) == 1 and segments[0].frames == MAX_CLIP_FRAMES + 1


def test_drift_report_accepts_scheduled_clips_and_rejects_late_ones():
    timeline = SpeechMediaTimeline(tone(25.0), RATE)
    segments = timeline.segments()
    anchor = 5_000_000
    lead = int(PUSH_LEAD_SECONDS * MICROSECONDS)
    video = [anchor + segment.start_us(RATE) + 9_000 for segment in segments]
    audio = [anchor + segment.start_us(RATE) - lead + 1_000 for segment in segments]
    report = timeline.measure_drift(anchor_us=anchor, video_started_us=video, audio_pushed_us=audio, push_lead_us=lead)
    assert report["profile"] == "ananta.meet-avatar-sync.v1"
    assert report["max_drift_us"] == 9_000 and len(report["segments"]) == len(segments)
    late = list(video)
    late[-1] += MAX_FRAME_DRIFT_US + 1
    with pytest.raises(ValueError, match="meet_avatar_drift_exceeded"):
        timeline.measure_drift(anchor_us=anchor, video_started_us=late, audio_pushed_us=audio, push_lead_us=lead)
    with pytest.raises(ValueError, match="meet_avatar_drift_observation_invalid"):
        timeline.measure_drift(anchor_us=anchor, video_started_us=video[:-1], audio_pushed_us=audio, push_lead_us=lead)


# --- state machine --------------------------------------------------------


def test_tts_start_and_end_drive_speaking_deterministically_with_soft_transitions():
    machine = AvatarStateMachine()
    assert machine.state == IDLE
    machine.transition(LISTENING, at=1.0)
    machine.transition(THINKING, at=2.0)
    machine.begin_speech(at=3.0)
    assert machine.state == SPEAKING
    mid = machine.body_language(3.0 + TRANSITION_SECONDS / 2)
    settled = machine.body_language(3.0 + TRANSITION_SECONDS)
    thinking = BODY_LANGUAGE[THINKING]
    assert thinking.head_tilt_degrees < mid.head_tilt_degrees < settled.head_tilt_degrees
    assert 0.0 < machine.transition_progress(3.1) < 1.0
    machine.end_speech(at=6.0)
    assert machine.state == IDLE
    with pytest.raises(ValueError, match="meet_avatar_timeline_not_monotonic"):
        machine.transition(THINKING, at=5.0)
    with pytest.raises(ValueError, match="meet_avatar_state_invalid"):
        machine.end_speech(at=7.0, resume=SPEAKING)


def test_states_have_distinct_body_language_and_idle_keeps_the_mouth_closed():
    silent = np.zeros(2 * RATE, dtype=np.float32)
    poses = {}
    for state in STATES:
        machine = AvatarStateMachine(initial=state)
        poses[state] = AnimationController(SpeechMediaTimeline(silent, RATE), machine).poses()
    assert all(pose.mouth == 0.0 for state in STATES for pose in poses[state])
    signatures = {
        state: (round(poses[state][6].head_tilt, 1), round(poses[state][6].gaze_dx, 1), round(poses[state][6].body_dy, 1))
        for state in STATES
    }
    assert len(set(signatures.values())) == 4


def test_blinks_are_independent_of_the_mouth_and_deterministic():
    samples = tone(6.0)
    controller = AnimationController(SpeechMediaTimeline(samples, RATE), AvatarStateMachine(initial=SPEAKING), seed="x")
    again = AnimationController(SpeechMediaTimeline(samples, RATE), AvatarStateMachine(initial=SPEAKING), seed="x")
    poses, repeat = controller.poses(), again.poses()
    assert [pose.blink for pose in poses] == [pose.blink for pose in repeat]
    blinks = [pose for pose in poses if pose.blink > 0]
    assert blinks and all(pose.mouth > 0 for pose in blinks)
    assert BlinkSchedule("a").blink_amount(0.0, 4.0) == 0.0


def test_speaking_poses_add_calm_head_motion_that_follows_the_envelope():
    samples = speech_with_pauses([(2.0, True), (1.0, False)])
    controller = AnimationController(SpeechMediaTimeline(samples, RATE), AvatarStateMachine(initial=SPEAKING))
    poses = controller.poses()
    voiced = [pose for pose in poses if pose.voiced]
    assert voiced and all(pose.head_dy < 0 for pose in voiced[3:])
    assert all(abs(pose.head_dx) <= 2.0 and abs(pose.head_dy) <= 3.0 and abs(pose.head_tilt) <= 4.0 for pose in poses)
    assert poses[-1].mouth == 0.0 and poses[-1].head_dy > -0.5


# --- renderer -------------------------------------------------------------


def test_render_distinguishes_states_open_mouth_and_blink():
    silent = np.zeros(RATE, dtype=np.float32)
    idle = render_pose(AnimationController(SpeechMediaTimeline(silent, RATE), AvatarStateMachine(initial=IDLE)).poses()[0])
    thinking = render_pose(
        AnimationController(SpeechMediaTimeline(silent, RATE), AvatarStateMachine(initial=THINKING)).poses()[0]
    )
    assert idle.size == (256, 256) and np.any(np.asarray(idle) != np.asarray(thinking))
    open_mouth = np.asarray(snake_frame(6, tone(1.0), RATE))
    closed_mouth = np.asarray(snake_frame(6, silent, RATE))
    assert np.any(open_mouth != closed_mouth)
    with pytest.raises(ValueError, match="meet_avatar_pose_invalid"):
        render_pose({"mouth": 1})


def test_build_video_renders_one_clip_segment_from_the_shared_timeline(tmp_path):
    samples = tone(12.0)
    data, frames = build_video(samples, RATE, 12.0, tmp_path, frames=MAX_CLIP_FRAMES, encoder=fake_encoder)
    assert frames == MAX_CLIP_FRAMES and data[4:8] == b"ftyp"
    tail, tail_frames = build_video(samples, RATE, 2.0, tmp_path / "tail", start_frame=MAX_CLIP_FRAMES, frames=24, encoder=fake_encoder)
    assert tail_frames == 24 and tail != data
    with pytest.raises(ValueError, match="meet_snake_frame_invalid"):
        build_video(samples, RATE, 1.0, tmp_path / "bad", start_frame=-1, encoder=fake_encoder)


def test_idle_clips_are_looping_silent_and_cached_per_state():
    clips = IdleClips(encoder=fake_encoder)
    first = clips.payload(THINKING)
    assert first["repeatMode"] == "loop" and first["classification"] == "synthetic"
    assert clips.payload(THINKING) is first and clips.payload(IDLE)["sha256"] != first["sha256"]
    with pytest.raises(ValueError, match="meet_avatar_state_invalid"):
        clips.payload(SPEAKING)


# --- publisher ------------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, seconds)


def fake_ports(clock, *, open_latency=0.004):
    events = []

    def avatar_open(payload):
        clock.now += open_latency
        events.append(("video", clock.now, payload["frames"]))
        return len([event for event in events if event[0] == "video"])

    def avatar_close(generation):
        events.append(("close", clock.now, generation))

    def speech_open(total):
        events.append(("speech_open", clock.now, total))
        return 7

    def speech_push(generation, offset, chunk):
        assert generation == 7 and len(chunk) <= FRAME_SAMPLES * 2
        events.append(("audio", clock.now, offset))
        clock.now += 0.0005

    return AvatarPorts(avatar_open, avatar_close, speech_open, speech_push, clock, clock.sleep), events


@pytest.mark.parametrize("seconds", [3.3, 31.0])
def test_publisher_keeps_clips_and_audio_on_one_timeline_for_long_answers(seconds):
    clock = FakeClock()
    ports, events = fake_ports(clock)
    samples = speech_with_pauses([(seconds / 2, True), (0.4, False), (seconds / 2, True)])
    pcm = pcm_bytes(samples)
    publisher = SpeechAvatarPublisher(ports, encoder=fake_encoder)
    generation, report = publisher.speak(pcm, current_generation=3)
    timeline = SpeechMediaTimeline(np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0, RATE)
    segments = timeline.segments()
    videos = [event for event in events if event[0] == "video"]
    assert len(videos) == len(segments) and generation == len(segments)
    assert [event[2] for event in videos] == [segment.frames for segment in segments]
    assert report["max_drift_us"] <= MAX_FRAME_DRIFT_US
    anchor = next(event[1] for event in events if event[0] == "audio")
    for segment, (_, at, _) in zip(segments, videos):
        assert abs(at - (anchor + segment.start_sample / RATE)) * MICROSECONDS <= MAX_FRAME_DRIFT_US
    # The previous idle generation was closed exactly once before the first clip.
    closes = [event for event in events if event[0] == "close"]
    assert closes[0][2] == 3 and len(closes) == len(segments)
    audio = [event for event in events if event[0] == "audio"]
    assert audio[0][2] == 0 and audio[-1][2] + FRAME_SAMPLES >= len(pcm) // 2
    assert clock.now >= anchor + len(pcm) // 2 / RATE - 0.01


def test_publisher_reports_drift_when_the_browser_stalls_a_clip_swap():
    clock = FakeClock()
    ports, _events = fake_ports(clock, open_latency=0.09)
    pcm = pcm_bytes(tone(21.0))
    with pytest.raises(ValueError, match="meet_avatar_drift_exceeded"):
        SpeechAvatarPublisher(ports, encoder=fake_encoder).speak(pcm)
