"""Fixed local NVENC working-set policy, not host-wide VRAM isolation.

Keep lookahead and B-frames disabled: FFmpeg otherwise increases the requested
surface count. Driver/context/model allocations remain outside this bound.
"""

NVENC_SURFACES = 4
NVENC_OPTIONS = (
    "-c:v",
    "h264_nvenc",
    "-preset",
    "p4",
    "-tune",
    "ull",
    "-pix_fmt",
    "yuv420p",
    "-surfaces",
    str(NVENC_SURFACES),
    "-rc-lookahead",
    "0",
    "-bf",
    "0",
    "-delay",
    "0",
    "-zerolatency",
    "1",
    "-multipass",
    "disabled",
    "-b:v",
    "350k",
)
