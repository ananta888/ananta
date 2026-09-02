"""Compatibility facade for the spreadsheet learning persistence port.

New code imports the neutral contract from :mod:`agent.ports.spreadsheet`.
"""

from __future__ import annotations

from agent.ports.spreadsheet import SpreadsheetLearningConflict, SpreadsheetLearningRepository

__all__ = ["SpreadsheetLearningConflict", "SpreadsheetLearningRepository"]
