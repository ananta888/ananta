# Bounded private browser startup diagnostics (MAP-29/30)

The first packaged-GPU browser attempt failed in 24.36 seconds before GPU
fixture setup, inside `DialogBrowserFixture.start`. Its older sandboxed browser
image emitted only `test_browser_launch_failed`; the actual underlying cause
cannot be recovered from that discarded error. It is neither a passing GPU
gate nor evidence of a packaged media-Worker defect. The subsequent direct
resource observation found available RAM/disk space; this does not prove what
happened at browser startup.

Keep the exact 15-second launch/20-second observation bounds, Chromium sandbox,
seccomp, non-root/read-only container, two CPUs, 1 GiB RAM and 256 PID limits.
Classify launch errors inside the existing private Node process into a closed
set: timeout, sandbox, resource, missing executable or unknown. Examine only
a bounded prefix and emit the fixed category, never raw Chromium logs, endpoint,
arguments, IDs or private paths. The Python reader accepts only these exact
categories and retains compatibility with the previous fixed error line.
Do not retry a failed launch or disable any isolation property.

Keep classification separate from the existing broad browser fixture (SRP).
Test the actual Node classifier with deterministic errors, redaction/bounds
and the Python projection. Add an explicit serial gate for three independently
owned sandboxed starts on fresh internal networks; cleanup only each test's
own browser/network. These are startup observations, not browser media,
multi-host/TURN, model readiness or production-release acceptance.

The 32 classifier/fixture regression tests passed in 22.54 seconds, including
the actual Node implementation, and all three independent real sandboxed
container starts passed in 13.44 seconds. No bounds or sandbox flags changed.
These repeats do not identify or repair the earlier generic startup failure;
future failures now retain their bounded category.
