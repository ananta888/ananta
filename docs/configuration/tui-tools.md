# TUI Tools & Default Editor Configuration

Ananta resolves the correct editor or TUI tool deterministically from config, never from LLM inference.

---

## Configuration files

Three config files are merged in order (later scopes override earlier ones):

| Scope   | Path                               | Override priority |
|---------|------------------------------------|-------------------|
| Global  | Built-in Ananta defaults           | Lowest            |
| User    | `~/.config/ananta/tui-tools.json`  | Medium            |
| Project | `.ananta/tui-tools.json`           | Highest           |

Two environment variables provide quick overrides without editing files:

| Variable                    | Effect                                          |
|-----------------------------|-------------------------------------------------|
| `TUI_DEFAULT_EDITOR`        | Override the global default editor command      |
| `TUI_ALLOW_ENVIRONMENT_EDITOR` | Set to `false` to suppress `$EDITOR`/`$VISUAL` |

---

## Full example config

```json
{
  "tui_tools": {
    "default_editor": "vim",
    "allow_environment_editor": true,
    "allowed_tools": [
      "vim", "nvim", "nano", "micro", "helix",
      "lazygit", "mc", "ranger"
    ],
    "filetype_editors": [
      { "match": "*.py",        "editor": "nvim", "args": ["{file}"] },
      { "match": "*.ts",        "editor": "nvim", "args": ["{file}"] },
      { "match": "*.md",        "editor": "vim",  "args": ["-c", "set ft=markdown", "{file}"] },
      { "match": "*.json",      "editor": "vim",  "args": ["-c", "set ft=json",     "{file}"] },
      { "match": "Dockerfile*", "editor": "vim",  "args": ["-c", "set ft=dockerfile", "{file}"] }
    ],
    "tool_profiles": [
      {
        "id": "git_ui",
        "command": "lazygit",
        "args": [],
        "working_directory": "{workspace}"
      },
      {
        "id": "file_manager",
        "command": "ranger",
        "args": ["{workspace}"]
      }
    ]
  }
}
```

---

## Editor resolution order

When opening a file Ananta applies the following 7-step resolution in order, using the first match:

1. `--with <editor>` argument passed directly to the CLI command  
2. Project-scope filetype rule (`.ananta/tui-tools.json`)  
3. User-scope filetype rule (`~/.config/ananta/tui-tools.json`)  
4. Global filetype rule (built-in defaults)  
5. `$EDITOR` or `$VISUAL` environment variable (only when `allow_environment_editor: true`)  
6. `default_editor` from merged config  
7. Hardcoded fallback: `vim`

---

## Known-safe tool names

These tools are shipped as the built-in allowlist:

- **Editors**: `vim`, `nvim`, `nano`, `micro`, `helix`
- **TUI tools**: `lazygit`, `mc`, `ranger`

Any tool not in the allowlist is rejected unless explicitly added via `allowed_tools`.

---

## Placeholder substitution

| Placeholder    | Resolves to                                  |
|----------------|----------------------------------------------|
| `{file}`       | Absolute path to the validated file          |
| `{workspace}`  | Absolute path to the authorized workspace root |

Placeholders are resolved before building the `argv` array — shell expansion never occurs.

---

## Read-only mode

Pass `--readonly` to the CLI commands or set `readonly: true` in API calls.  
The resolver appends `-R` for Vim/Neovim. Other editors receive the flag only when
`readonly_supported` is `true` in their profile.

---

## Common recipes

### Use Neovim for Python, keep Vim for everything else (user config)

```json
{
  "tui_tools": {
    "allowed_tools": ["vim", "nvim"],
    "filetype_editors": [
      { "match": "*.py", "editor": "nvim", "args": ["{file}"] }
    ]
  }
}
```

### Disable environment-editor pickup

```json
{
  "tui_tools": {
    "allow_environment_editor": false
  }
}
```

Or via env var:

```bash
export TUI_ALLOW_ENVIRONMENT_EDITOR=false
```

### Add a custom project-level TUI tool

```json
{
  "tui_tools": {
    "allowed_tools": ["vim", "lazygit", "tig"],
    "tool_profiles": [
      {
        "id": "git_log",
        "command": "tig",
        "args": ["--all"],
        "working_directory": "{workspace}"
      }
    ]
  }
}
```

Then launch with:

```bash
ananta tui --tool git_log
ananta tmux tool git_log
```

---

## Related docs

- `docs/cli/commands.md` — CLI reference for `ananta tui` and `ananta tmux`
- `docs/security/terminal-sessions.md` — Security model and permission classes
