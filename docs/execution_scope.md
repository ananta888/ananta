# Execution scope and workspace metadata (API guidance)

Expose execution scope and workspace lifecycle metadata in execution responses and read models.

Suggested fields

- execution_scope: { repo: string, branch: string, ref: string|null }
- workspace_id: string
- isolation_mode: "ephemeral" | "shared" | "readonly"
- lease_id: string (container or workspace lease identifier)
- lifecycle_status: "allocated" | "preparing" | "running" | "cleaning_up" | "released"

Example API response snippet

{
  "task_id": "t123",
  "status": "completed",
  "execution": {
    "execution_scope": { "repo": "org/repo", "branch": "main" },
    "workspace_id": "ws-abc123",
    "isolation_mode": "ephemeral",
    "lease_id": "lease-987",
    "lifecycle_status": "released"
  }
}

Operators should persist workspace lifecycle records in the hub so retries and cleanups remain deterministic.
