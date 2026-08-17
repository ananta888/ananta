# Incremental builds

Workers receive a ChangeSet and emit verified layer blobs. They do not
move the head. The Hub compare-and-swaps `generation` and rejects stale
publishers.
