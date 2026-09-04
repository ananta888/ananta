#!/usr/bin/env python3
"""Run the 35B-A3B offload profile through the generic resource probe."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark.ornith_resource_profile import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
