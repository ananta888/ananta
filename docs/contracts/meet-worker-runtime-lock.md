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
