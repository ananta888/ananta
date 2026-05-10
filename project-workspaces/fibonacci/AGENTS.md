# AGENTS.md

This is a scoped OpenCode workspace for the Ananta project.

## Mandatory architecture rules
- The hub remains the control plane and owns orchestration, routing, policy, and the task queue.
- Workers execute delegated work only.
- Do not introduce worker-to-worker orchestration.
- Preserve container boundaries and avoid implicit shared state.
- Prefer additive, backward-compatible changes over breaking redesigns.

## Execution environment constraints
- Do NOT use `sudo` — the execution environment is a Docker container without root privileges.
- Do NOT use `su`, `sudo -i`, or any privilege escalation command.
- Do NOT use `systemctl` — there is no systemd in this Docker container.
- Do NOT use `service` — init.d service management is unavailable in this container.
- Do NOT use `ss` — not installed. Use `netstat -tlnp` or `cat /proc/net/tcp` for port info.
- To check if a process is running use `pgrep -x <name>` or `ps aux`.
- To check open ports use `netstat -tlnp` or `cat /proc/net/tcp`.
- If a task requires systemd/root/service management, describe the required manual step in a comment instead of running it.
- Shell commands must work as a non-root user inside a container.
- If the target software (nginx, apache, mysql, etc.) is not installed in this container: do NOT run the command directly. Instead use a `write_file` tool_call to write the commands as a shell script file in the artifacts directory.

## Engineering rules
- Keep changes small, testable, and SOLID.
- Reuse existing abstractions before adding new ones.
- Keep behavior observable; do not hide failures.
- Respect the task workspace as the primary place for new files and generated context.
- The workspace may be reused across related delegated tasks; keep state intentional and auditable.

## Workspace guidance
- Read `.ananta/context-index.md` first for task-specific context files.
- Use `rag_helper/` for retrieved research and knowledge files when present.
- Follow `.ananta/response-contract.md` for the required response format.
