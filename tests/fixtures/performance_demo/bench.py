from __future__ import annotations

import time

from slow_math import sum_to_n


started = time.perf_counter()
value = sum_to_n(20000)
elapsed = time.perf_counter() - started
print(f"sum_to_n: {elapsed:.6f} seconds")
print(f"value={value}")
