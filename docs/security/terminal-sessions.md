# Terminal Sessions — Security Model

Ananta's embedded editor and TUI tool features operate on a strict deny-by-default
security model. This document describes permission classes, hard denies, and target
classification.

---

## Target types and permission classes

| Target type      | Default editor launch | Default tool launch | Config key                         |
|------------------|-----------------------|---------------------|------------------------------------|
| Worker           | allowed               | allowed             | `worker_tools_enabled: true`       |
| Hub              | **denied**            | **denied**          | `hub_tools_enabled: false`         |
| Hub-as-Worker    | **denied**            | **denied**          | `hub_as_worker_tools_enabled: false` |

Hub and Hub-as-Worker targets are high-risk because they control the orchestration
plane. They must be explicitly enabled in policy and require separate permission grants.

---

## Permission names

| Permission                    | What it allows                                          |
|-------------------------------|---------------------------------------------------------|
| `tui.editor.open`             | Open a file in the resolved default editor              |
| `tui.editor.write`            | Launch an editor that can write file changes            |
| `tui.editor.readonly`         | Launch an editor in read-only mode                      |
| `tui.editor.custom_command`   | Override editor with an arbitrary command (denied by default for remote) |
| `tui.tool.list`               | List available embedded TUI tools                       |
| `tui.tool.launch`             | Launch an allowed embedded TUI tool                     |
| `tui.tool.worker.launch`      | Launch tool against Worker target                       |
| `tui.tool.hub.launch`         | Launch tool against Hub target (high-risk)              |
| `tui.tool.hub_as_worker.launch` | Launch tool against Hub-as-Worker target              |

---

## Hard denies — never allowed regardless of config

The following actions are permanently blocked by the security layer:

- **Editor command from untrusted LLM output** — editor/tool selection is always registry-driven, never inferred from model output.
- **File open outside authorized workspace** — paths are resolved with `os.path.realpath` and checked against the workspace root before any process is started.
- **Path traversal in file argument** — `../../` sequences are resolved and rejected.
- **Custom tool command without explicit permission** — arbitrary command strings require `tui.editor.custom_command` to be granted.
- **Hub embedded tool without Hub tool permission** — Hub target requires `tui.tool.hub.launch`, which is off by default.
- **Write-capable editor when only read-only permission exists** — `tui.editor.write` must be present when the editor is launched in writable mode.
- **Shell expansion in file argument** — commands are built as `argv` arrays; `shell=True` is never used for file path arguments.

---

## Path validation

Every file open action goes through `WorkspacePathValidator` before any process is
launched:

1. Resolve the path with `os.path.realpath` to follow symlinks.
2. Check that the resolved path starts with `<workspace>/` (trailing separator prevents `/workspace` from matching `/workspace-other`).
3. Reject if the realpath points outside the workspace root (symlink escape).
4. Reject relative traversals that escape the workspace after resolution.
5. Return one of five reason codes: `ok`, `invalid_path`, `outside_workspace`, `path_traversal`, `symlink_escape`.

---

## Command construction

Editor and tool commands are always constructed as `argv` arrays:

```python
# safe — argv array, no shell
os.execvp("vim", ["vim", "/workspace/app.py"])

# NEVER done — shell=True is not used for file paths
subprocess.run(f"vim {file_path}", shell=True)  # not in Ananta
```

The `{file}` and `{workspace}` placeholders in tool profile definitions are resolved
to validated absolute paths before the `argv` array is finalized.

---

## Audit trail

Every editor and tool launch records:

- session ID
- session type (`embedded_editor` or `embedded_tool`)
- target type (`worker`, `hub`, `hub_as_worker`)
- workspace root
- file path (for editors)
- tool ID (for tools)
- launched-at timestamp
- readonly flag

Session metadata is stored separately from the live terminal session and survives
until the session is explicitly closed.

---

## Remote vs local-dev defaults

| Setting                          | Local dev | Remote/production |
|----------------------------------|-----------|-------------------|
| `hub_tools_enabled`              | false     | false             |
| `worker_tools_enabled`           | true      | true              |
| `allow_write_editor`             | true      | true              |
| `allow_custom_editor_command`    | true      | **false**         |

Custom editor commands are disabled by default for remote sessions because they
allow arbitrary binary execution within the workspace. Enable them only after
explicit operator review.

---

## Related docs

- `docs/configuration/tui-tools.md` — Editor resolution and tool config
- `docs/cli/commands.md` — CLI reference for `ananta tui` and `ananta tmux`
