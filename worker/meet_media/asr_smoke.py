"""Synthetic local GPU smoke, never a real Meet receive grant or release gate."""

import json
import os
import tempfile
import time
from pathlib import Path

from ananta_contracts.meet_audio_profile import parse_audio_profile
from voice_runtime.preprocessing.audio_decode import AudioDecodeLimits, SafeAudioDecoder
from worker.meet_media.asr_model import REVISION
from worker.meet_media.asr_pipeline import MeetAsrPipeline
from worker.meet_media.audio_receive import MeetAudioReceiver, ReceiveBinding
from worker.meet_media.speech import speech


def run(audio_profile=None):
    started = time.monotonic()
    profile = parse_audio_profile(audio_profile) if audio_profile is not None else None
    if profile is not None and profile.language != "de":
        raise ValueError("meet_asr_smoke_language_invalid")
    binding = ReceiveBinding(
        tenant_id="synthetic",
        project_id="synthetic",
        task_id="synthetic-asr-task",
        lease_id="synthetic-lease",
        runtime_id="synthetic-runtime",
        session_id="synthetic-session",
        generation=1,
        room_id="room-111111111111111111",
        membership_epoch=1,
        peer_id="synthetic-other-peer",
        own_peer_id="synthetic-self",
        publication_id="synthetic-source",
        publication_epoch=1,
        source="microphone",
    )

    class SyntheticLease:
        def require(self, requested):
            if requested != binding or time.monotonic() - started > 60:
                raise ValueError("synthetic_lease_expired")

    with tempfile.TemporaryDirectory(prefix="meet-asr-smoke-") as temporary:
        path = Path(temporary) / "synthetic.wav"
        speech("Hallo, dies ist ein Test für die lokale Spracherkennung.", path)
        audio = SafeAudioDecoder(limits=AudioDecodeLimits(max_duration_ms=10_000)).decode(
            filename="synthetic.wav", payload=path.read_bytes()
        )
        pcm = audio.pcm_s16le
        pcm += b"\0" * ((-len(pcm)) % 320)  # Explicit final synthetic packet padding, <10 ms.
        if profile is not None:
            if len(pcm) > profile.end_sample * 2:
                raise ValueError("meet_asr_smoke_segment_too_short")
            pcm += b"\0" * (profile.end_sample * 2 - len(pcm))
        lease = SyntheticLease()
        pipeline = MeetAsrPipeline(
            binding,
            lease,
            deadline_monotonic=time.monotonic() + 30,
            **({"audio_profile": profile.projection()} if profile is not None else {}),
        )
        receiver = MeetAudioReceiver(
            binding,
            lease,
            pipeline,
            **(
                {"language": profile.language, "max_audio_seconds": profile.segment_seconds}
                if profile is not None
                else {}
            ),
        )
        try:
            for offset in range(0, len(pcm), 3200):
                receiver.push(binding, start_sample=offset // 2, pcm=pcm[offset : offset + 3200])
            transcript = receiver.finish()
        finally:
            receiver.close()
    normalized = transcript.text.casefold()
    matches = sum(word in normalized for word in ("hallo", "test", "lokale", "spracherkennung"))
    if matches < 3:
        raise ValueError("meet_asr_synthetic_phrase_not_recognized")
    return {
        "classification": "synthetic_local_technical_observation",
        "status": "passed",
        "engine": "faster-whisper-cuda",
        "model_revision": REVISION,
        "received_samples": transcript.end_sample - transcript.start_sample,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "matched_expected_words": matches,
        "production_release_evidence": False,
        "real_meet_receive_verified": False,
        "human_capture_used": False,
        **({"audio_profile": profile.projection()} if profile is not None else {}),
    }


if __name__ == "__main__":
    from ananta_contracts.meet_audio_profile import AudioReceiveProfile

    selected = os.environ.get("MEET_ASR_SMOKE_PROFILE", "legacy")
    profiles = {
        "legacy": None,
        "bounded-4s-vad": AudioReceiveProfile(segment_seconds=4).projection(),
        "bounded-4s-no-vad": AudioReceiveProfile(segment_seconds=4, vad="off").projection(),
    }
    if selected not in profiles:
        raise ValueError("meet_asr_smoke_profile_invalid")
    print(json.dumps(run(profiles[selected])))
