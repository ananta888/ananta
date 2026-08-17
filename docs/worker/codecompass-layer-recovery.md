# Layer recovery

- Digest mismatch on read is fail-closed.
- Rollback points the head at a historical layer id; layers stay immutable.
- Dangling `.staging` directories are unpublished and can be deleted.
- Rebuild-from-source is the last resort and must not invent records.
