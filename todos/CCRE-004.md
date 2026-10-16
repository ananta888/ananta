# CCRE-004

## Status
Done

## Summary
Tool-Lizenz-, API-, Wartungs- und Plattformmatrix für Ghidra, LIEF, Rizin/Cutter, angr, Frida, REA, RetDec, ILSpy, JADX, radare2, IDA und Binary Ninja erstellt in `docs/tooling-matrix/Ghidra-LIEF-RizinCutter-angr-Frida-REA-RetDec-ILSpy-JADX-radare2-IDA-Binary-Ninja.md`.

## Verification
Run: `python -m pytest -q --confcutdir=tests/self_development -o addopts= -o cache_dir=/tmp/ananta-self-dev-cache tests/self_development`
