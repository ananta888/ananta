# Worker Workspace Model

## Objective

The worker runs in an isolated workspace/worktree and must not mutate the main working tree by default.

## Workspace zones

- **allowed_roots**: read scope roots provided by Hub constraints.
- **sandbox/worktree**: mutable execution area for proposed/apply/test operations.
- **output_paths**: explicit artifact/log destinations.
- **main tree**: read-only unless approval-gated apply explicitly permits controlled write-back.

## Constraints

- maximum file count
- maximum byte budget
- allowed shell commands
- writable output prefixes
- flag for controlled main-tree apply

## Safety rules

- Any write outside the sandbox/output allow-list is denied.
- Patch apply to main tree is blocked unless explicit approval binding is present.
- Command execution must use constrained cwd + environment.
- Cleanup removes temporary worktree/sandbox state after completion.

## Environment modes

- **local developer mode**: uses temp worktree under local repo root.
- **CI mode**: uses temp worktree + deterministic cleanup + bounded artifacts for diagnostics.
