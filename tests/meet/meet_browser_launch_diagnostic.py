"""Closed browser-launch error codes; never forward Chromium logs or arguments."""

LAUNCH_FAILURE_CODES = frozenset({"timeout", "sandbox", "resource", "executable", "unknown"})

CLASSIFY_LAUNCH_FAILURE = """function classifyLaunchFailure(error) {
  if (error?.name === 'TimeoutError') return 'timeout';
  const message = typeof error?.message === 'string' ? error.message.slice(0, 32768) : '';
  if (/No usable sandbox|Failed to move to new namespace|Operation not permitted.*namespace/i.test(message))
    return 'sandbox';
  if (/Resource temporarily unavailable|Cannot allocate memory|No space left on device/i.test(message))
    return 'resource';
  if (/Executable doesn't exist|browser executable.*not found/i.test(message)) return 'executable';
  return 'unknown';
}"""


def launch_failure(line):
    if line == "test_browser_launch_failed":
        return "test_browser_sandbox_launch_failed"  # Older fixture output remains recognizable.
    prefix = "test_browser_launch_failed:"
    if line.startswith(prefix) and line[len(prefix) :] in LAUNCH_FAILURE_CODES:
        return "test_browser_sandbox_launch_failed_" + line[len(prefix) :]
    return None
