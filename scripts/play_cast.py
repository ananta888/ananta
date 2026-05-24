#!/usr/bin/env python3
"""
Minimal asciinema v2 cast player for the terminal.
Usage:  python scripts/play_cast.py tests/output/operator_tui_e2e.cast [--speed N]
"""
from __future__ import annotations

import argparse
import json
import sys
import time

_CLEAR = "\x1b[2J\x1b[H"


def play(path: str, speed: float = 1.0, pause: bool = False) -> None:
    with open(path, encoding="utf-8") as f:
        lines = f.read().strip().split("\n")

    header = json.loads(lines[0])
    labels: dict[str, str] = header.get("labels", {})
    title  = header.get("title", path)
    events = [json.loads(l) for l in lines[1:]]

    print(f"\x1b[1m{title}\x1b[0m  ({len(events)} frames, {speed}x speed)")
    if pause:
        input("Press Enter to start…")

    prev_t = 0.0
    for ev in events:
        t, kind, data = ev
        if kind != "o":
            continue

        # find label for this timestamp
        label = labels.get(f"{t:.1f}", "")

        delay = (t - prev_t) / speed
        if delay > 0:
            time.sleep(delay)
        prev_t = t

        sys.stdout.write(data)
        if label:
            sys.stdout.write(f"\x1b[s\x1b[{header['height']};0H\x1b[2K"
                             f"\x1b[2m── {label} ──\x1b[0m\x1b[u")
        sys.stdout.flush()

    # move cursor below last frame
    sys.stdout.write(f"\x1b[{header['height'] + 1};0H\n")
    print("\x1b[2mdone\x1b[0m")


def main() -> None:
    ap = argparse.ArgumentParser(description="Play an asciinema v2 cast file")
    ap.add_argument("cast", help="path to .cast file")
    ap.add_argument("--speed", type=float, default=1.0, help="playback speed multiplier")
    ap.add_argument("--pause", action="store_true", help="wait for Enter before starting")
    args = ap.parse_args()
    play(args.cast, speed=args.speed, pause=args.pause)


if __name__ == "__main__":
    main()
