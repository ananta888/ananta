"""Worker-side facade over architecture intelligence projections."""

from ananta_codecompass.architecture_intelligence.analyze import analyze_architecture
from ananta_codecompass.architecture_intelligence.diff import diff_graphs

__all__ = ["analyze_architecture", "diff_graphs"]
