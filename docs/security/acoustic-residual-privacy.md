# Acoustic residual privacy gate

The optional residual is a correction hint, not audio, identity evidence or a
training source. It contains at most 128 six-bit block-energy summaries and no
phase, waveform, local path, device identifier or speaker embedding. Extraction
checks an explicit `residual_feature` grant before reading PCM and purges its
reachable buffer when the grant is absent or revoked.

The source-bound deterministic attack gate
`scripts/benchmark/acoustic_residual_privacy.py` evaluates 96 one-second,
speech-like utterances from eight synthetic profiles. Six utterances per
profile train a nearest-centroid linkability attack and six remain held out.
It measures a public zero-phase block-envelope reconstruction attack, speaker
linkability advantage over chance and the best threshold membership advantage
for four enrolled versus four non-enrolled profiles. Only aggregate scores are
written; generated PCM and residual vectors remain process-local.

The gate treats reconstructability above 0.30, speaker linkability above 0.25
or membership-inference advantage above 0.15 as a No-Go. The current measured
membership advantage is above its threshold, so
`MEASURED_PRIVACY_VERDICT = "no_go"` is enforced in the production adapter.
Even a structurally low-detail vector is therefore not admitted. Invalid,
non-finite or over-budget values are also assigned maximal risk. A peer,
AI-Snake prompt or capability claim cannot override this decision. Transcript-
first and ordinary encrypted audio remain available.

The synthetic corpus makes this a reproducible implementation gate, not a
production-population or formal-anonymity claim. A future Go requires stronger
reduction, a version bump, an approved representative/consented corpus,
independent privacy review and a newly source-bound gate artifact. Until then,
the measured No-Go is the only production policy.
