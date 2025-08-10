"""Ensure project root is importable for tests."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Default DB URL for local testing if not provided
os.environ.setdefault("DATABASE_URL", "postgresql://postgres@localhost:5432/ananta")
