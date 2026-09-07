"""Select a bounded asset renderer; neither adapter owns policy or dispatch."""

import time

from ananta_contracts.meet_persona_video import decode_assignment
from worker.meet_media.persona_clip_frames import render_persona_clip
from worker.meet_media.persona_video import persona_video


def stored_clip(turn, audio, duration, directory, *, require_current):
    require_current()
    assignment = turn["persona_video"]
    clip = decode_assignment(assignment, tenant_id=turn["tenant_id"], project_id=turn["project_id"])
    remaining = turn["deadline"] - time.time()
    if remaining <= 0:
        raise ValueError("meet_turn_expired")
    return render_persona_clip(
        clip,
        audio,
        duration,
        directory,
        origin_kind=assignment["origin_kind"],
        classification=assignment["reference"]["classification"],
        repeat_mode=assignment["repeat_mode"],
        require_current=require_current,
        # Clip decoding has its own <=30s contract, not the complete 115s
        # media-turn budget. Never extend either the parent or decoder limit.
        deadline_monotonic=time.monotonic() + min(20, remaining),
    )


def render_visual(turn, audio, duration, directory, *, require_current):
    selected = [
        (name, engine, render)
        for name, engine, render in (
            ("persona_image", "persona-image-h264_nvenc", persona_video),
            ("persona_video", "persona-clip-h264_nvenc", stored_clip),
        )
        if name in turn
    ]
    if len(selected) != 1:
        raise ValueError("meet_persona_visual_selection_invalid")
    name, engine, render = selected[0]
    path = render(turn, audio, duration, directory, require_current=require_current)
    require_current()
    return path, engine, {name: turn[name]["reference"]}
