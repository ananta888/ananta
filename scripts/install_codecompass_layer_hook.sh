#!/bin/sh
# Install only the CodeCompass post-commit hook into this checkout's .git/hooks.
# Deliberately does not set core.hooksPath, which would also activate the other
# hooks in git-hooks/ (e.g. the full pre-push pipeline).
set -e
repo=$(git rev-parse --show-toplevel)
hooks=$(git rev-parse --git-path hooks)
mkdir -p "$hooks"
if [ -e "$hooks/post-commit" ] && ! grep -q codecompass_layer_sync "$hooks/post-commit"; then
  echo "a different post-commit hook exists at $hooks/post-commit; not overwriting" >&2
  exit 1
fi
cp "$repo/git-hooks/post-commit" "$hooks/post-commit"
chmod +x "$hooks/post-commit"
echo "installed $hooks/post-commit"
