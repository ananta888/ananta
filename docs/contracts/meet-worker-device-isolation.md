# Explicit Worker device and root-filesystem boundaries (MAP-10)

## Source audit before implementation

At `f333c4826`, the standalone Worker has CPU/RAM/PID/tmpfs limits, private
state/model/key mounts, non-root execution, capability drop and Chromium
sandboxing. Packaged liveness and actual GPU fixtures already use a read-only
root filesystem. The shipped Compose template does not: it also requests
`gpus: all` for both media Worker and local model provider. Do not mark the
broader isolation criterion complete from more restricted test fixtures.

Make the Worker's root filesystem read-only in the template; retain the exact
existing writable state bind and bounded `/tmp`/shared-memory locations.
Select one GPU device by default (`0`) for both services, with an explicit
operator environment override for another device ID. Preserve the same image,
capabilities, service boundaries and ports. No running service is recreated.

Docker supports explicit GPU device IDs; this restricts device visibility, not
exclusive use or a VRAM partition. The existing host-driver overlay remains an
explicit GPU-0-only alternative and resets toolkit GPU requests; the new toolkit
selector must not be advertised as changing that overlay.
See [Docker's GPU device selection](https://docs.docker.com/compose/how-tos/gpu-support/).

Check raw template invariants and the locally installed Compose renderer with
default and explicit device IDs, using an empty environment file and synthetic
paths instead of loading operator secrets. Inspect the actual owned packaged
container's read-only-root flag alongside its existing liveness gate. Existing
packaged GPU component/browser checks already demonstrate inference with a
read-only root; no need to replace a serving Worker to test configuration.

SRP: Docker configuration owns device/filesystem restrictions; Hub scheduling,
model execution and source authorization do not gain device-selection policy.
Network egress, aggregate GPU capacity/fairness, multi-agent and production
rollout remain separate limitations. In particular the existing bridge is not
a destination allowlist, and one visible GPU is not reserved exclusively.
