# Spreadsheet learning persistence and split locks

Spreadsheet learning is a Hub-owned control-plane lifecycle. Feedback, privacy preview, consent,
dataset admission, immutable split decisions, training lineage and revocation impact remain separate
states. Applying a proposal or recording feedback never grants training consent.

In production `worker` mode the Hub uses the central SQL repository. The file-backed SQLite store is
only the compatible mock-mode adapter. Both implement the focused
`SpreadsheetLearningRepository` boundary; Workers do not own or mutate the task queue.

Before a dataset artifact is written, records are processed in this fixed order:

1. verify tenant, owner, feedback eligibility, exact masked-record consent and expiry;
2. deterministically remove exact record duplicates;
3. form connected leakage groups from document lineage, instruction-template fingerprints,
   formula-family fingerprints and bounded SimHash/LSH near-duplicate candidates;
4. assign whole clusters to train, validation, eval or test;
5. bind assignments, exclusions, distribution warnings and reproducibility evidence into
   `ananta.spreadsheet-dataset-split-lock.v1`.

Dataset identities are immutable. Replaying an identity with a changed manifest fails closed, so no
API or UI path can silently move a record between splits. Consent revocation and its automatic fencing
intent are committed in one database transaction. Affected datasets become unavailable for future
training; jobs are cancelled automatically where possible, and terminal lineage is quarantined. The
system records adapter deprecation/retraining impact and never claims mathematical unlearning.

The policy implements the accepted requirements associated with `SRC_0003` and `SRC_0005`; privacy,
consent and revocation decisions additionally remain bound to the exact projector, masking, serializer,
policy, document-version and record digests. Tests contain no human approval gate.
