# Explicit Python runtime lock for the Meet Worker (MAP-10)

## Source audit before implementation

At `8645183bf`, the Python base digest and six direct media packages are pinned,
but CUDA libraries, browser dependencies and other transitive Python packages
are resolved afresh on each build. The image healthcheck does not validate
that dependency inventory. A fresh dependency build exceeded its ten-minute
budget during large CUDA-wheel installation; source packaging on an installed
image is not proof of a successful clean build.

A non-root, network/GPU-free, read-only inventory of locally installed image
`sha256:0e112df54c364f1672890840dcf94986274bfd0c356fe2a1bbab4680ce9200bf`
provides an explicit tested-runtime baseline. Inventory does not execute
inference or grant production acceptance. Do not choose newer package versions
or copy any runtime key/model data when adding the lock.

Add an exact normalized Python distribution/version lock for that inventory.
Apply it as constraints to both existing dependency-install steps. Keep the
existing Piper CPU-ONNX dependency replacement explicit: the temporary CPU
distribution is constrained during build, uninstalled, and must be absent in
the final image. The GPU distribution must match its existing pin. Do not
disable dependency resolution or silently accept an unknown distribution.

Add a small standard-library inventory checker with closed lock parsing and
exact installed-set/version comparison. Run it during the image build after
the browser installation; failures report fixed bounded diagnostics, not
arbitrary metadata. Keep this build concern out of health/task/Hub policy.
Unit tests cover duplicate/invalid/unpinned entries, missing/extra/wrong
versions, CPU-distribution reintroduction and direct-requirement agreement.
Check the real installed inventory, then repeat the full build with an
explicit longer bound. Do not replace any running image or service.

This pins Python versions and detects inventory drift; it is not a wheel-hash
lock, immutable APT snapshot, reproducible image digest, GPU readiness claim
or production evidence. OS/browser artifact hashing and clean-build outcome
must remain explicit rather than inferred from a matching package list.

SRP: a build-only checker owns inventory validation; Docker owns installation
and existing Worker modules retain delegated runtime execution. No new global
service, dependency resolver, worker orchestration or broad configuration API.

## Implemented inventory gate

The lock contains 43 exact distribution pins: 42 in the final runtime and the
explicit temporary CPU-ONNX build dependency. Both media and Playwright installs
use the same constraints; the existing GPU-ONNX replacement remains unchanged.
After Playwright installation, the image runs the standalone inventory checker.
Any missing, extra, changed or duplicate-normalized distribution fails the build;
the CPU distribution is forbidden in the final set.

The checker is standard-library-only and separate from Worker execution. Its
16 KiB/256-entry ASCII lock parser rejects version ranges, URLs, markers,
duplicate/ambiguous names and malformed versions. File reads are nonblocking,
regular-file-only and bounded; descriptor cleanup covers failure. CLI output is
only a fixed success/failure line, including FIFO and malformed-metadata cases.
No package install, network call, source registration or task dispatch occurs
inside this checker.

The first 32-test run had 31 passes, including the actual immutable-container
inventory, and one test assertion failure in 27.78 seconds: the expected entry
count was mistakenly 44 instead of 43. Correcting that assertion passed in
7.25 seconds; no package pin was changed to force a match. The final combined
TTS/observer/inventory regression passed all 210 tests in 79.14 seconds with
the real inventory gate explicitly enabled and no skips. Targeted Ruff passes.
The full fresh dependency-image build still follows these checks; its outcome
is not inferred from matching the existing runtime.

## Successful full build and packaged verification

The complete Dockerfile build subsequently finished within its explicit
1,800-second bound, using the source snapshot `76ec57d71`. The resulting image
is `sha256:3444cb7d1124c52959be70043b4c66fa78bba0e55f109c18b724d2ab46dddb9e`.
Its build log records dependency installation, the successful inventory check,
browser installation and completed image export; this is not a source-only
repackage of the old runtime.

All 34 inventory tests passed in 28.42 seconds with the real container gate
explicitly enabled against that new image. The packaged health gate also
passed in 26.03 seconds, without source bind mounts, including the actual
healthy/unhealthy/healthy transition and no Hub package or executed lease.
No running Worker or provider was replaced. These checks establish a successful
locked-version build and liveness, not inference correctness, wheel/APT hash
reproducibility or Registry-backed production release evidence.
