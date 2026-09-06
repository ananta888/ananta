"""Private worker clip import: bounded decoder, mute normalization and preview."""

import hashlib

from ananta_contracts.persona_video import (
    MAX_INPUT_BYTES,
    MAX_PREVIEW_BYTES,
    MAX_VIDEO_BYTES,
    SanitizedPersonaVideo,
    decode_video,
    encode_video,
)
from worker.meet_media.persona_video_processes import FFMPEG, PIPE_INPUT, PersonaVideoProcesses


class PersonaVideoInspector:
    def __init__(self, *, require_current, deadline_monotonic, runner=None):
        self.processes = PersonaVideoProcesses(
            require_current=require_current,
            deadline_monotonic=deadline_monotonic,
            runner=runner,
        )
        self.require_current = require_current

    def inspect(self, content, media_type):
        if (
            type(content) is not bytes
            or not 16 <= len(content) <= MAX_INPUT_BYTES
            or content[4:8] != b"ftyp"
            or media_type != "video/mp4"
        ):
            raise ValueError("persona_video_input_invalid")
        try:
            self.processes.probe(content)
            video = self.processes.run(
                [
                    *FFMPEG,
                    *PIPE_INPUT,
                    "-map",
                    "0:v:0",
                    "-an",
                    "-sn",
                    "-dn",
                    "-map_metadata",
                    "-1",
                    "-filter_threads",
                    "1",
                    "-vf",
                    "scale=256:256:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                    "pad=256:256:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=12,setpts=N/(12*TB)",
                    "-frames:v",
                    "120",
                    "-t",
                    "10",
                    "-c:v",
                    "libx264",
                    "-threads",
                    "1",
                    "-preset",
                    "ultrafast",
                    "-pix_fmt",
                    "yuv420p",
                    "-b:v",
                    "512k",
                    "-maxrate",
                    "512k",
                    "-bufsize",
                    "1024k",
                    "-movflags",
                    "frag_keyframe+empty_moov+default_base_moof",
                    "-f",
                    "mp4",
                    "pipe:1",
                ],
                content,
                MAX_VIDEO_BYTES,
                8,
            )
            frames = self.processes.probe(video, normalized=True)
            preview = self.processes.run(
                [
                    *FFMPEG,
                    *PIPE_INPUT,
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-an",
                    "-sn",
                    "-dn",
                    "-c:v",
                    "png",
                    "-threads",
                    "1",
                    "-pix_fmt",
                    "rgba",
                    "-f",
                    "image2pipe",
                    "pipe:1",
                ],
                video,
                MAX_PREVIEW_BYTES,
                3,
            )
            result = SanitizedPersonaVideo(
                hashlib.sha256(content).hexdigest(),
                hashlib.sha256(video).hexdigest(),
                hashlib.sha256(preview).hexdigest(),
                frames,
                video,
                preview,
            )
            # Apply the same closed byte/hash/header bounds as the eventual Hub
            # receipt consumer; normalization does not admit a source identity.
            result = decode_video(encode_video(result), result.source_sha256)
            self.require_current()
            return result
        except Exception:
            raise ValueError("persona_video_inspection_failed_or_revoked") from None
