# Bitcoin Mining Demo (Toy Fixture)

This fixture is a deterministic **toy** mining flow for Ananta source-grounding tests.

Important:
- This is not real Bitcoin mining.
- It uses an artificial easy target (`00...`) so a valid nonce can be found quickly.
- The purpose is deterministic evidence generation for `SRC_*` and `RUN_*` citation checks.

The script `mining_demo.py` builds a simplified header from:
- version
- previous block hash
- merkle root
- fixed timestamp
- toy bits marker
- nonce

Then it computes `double_sha256(header)` and scans a bounded nonce range.

Use this fixture to validate:
- explanatory claims citing source text (`SRC_*`)
- numeric hash/nonce claims citing tool-run output (`RUN_*`)
