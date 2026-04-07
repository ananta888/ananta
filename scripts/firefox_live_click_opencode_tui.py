#!/usr/bin/env python3
import sys

from firefox_live_click_terminal_focus import main as terminal_focus_main


def main() -> None:
    args = list(sys.argv[1:])
    if "--interactive-launch-mode" not in args:
        args = ["--interactive-launch-mode", "tui", *args]
    terminal_focus_main(args)


if __name__ == "__main__":
    main()
