"""Operator-grade terminal UI surface for Ananta."""

__all__ = ["main"]


def main() -> None:
    """Run the Operator TUI without importing the full app at package import time."""
    from client_surfaces.operator_tui.app import main as _main

    _main()
