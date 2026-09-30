"""Repository paths used by the semantic-media program release gate."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TODO = ROOT / "todos/archiv/todo.ai-snake-semantic-media-speech-program.json"
SCHEMA = ROOT / "schemas/release/semantic_media_program_evidence.v1.json"
OUTPUT = ROOT / "artifacts/test-gates/semantic-media-program-evidence.json"
