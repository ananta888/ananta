# Editor/TUI Release Packaging and Versioning Strategy

## Packaging targets

- Neovim plugin package (semantic versions)
- TUI package (semantic versions)
- Shared compatibility matrix against Ananta backend versions

## Versioning rules

- Use semantic versioning for both plugin and TUI.
- Track minimum supported backend API contract version.
- Breaks in contract require a major version bump or compatibility adapter.

## Compatibility policy

- Maintain backward compatibility where possible.
- Prefer additive contract extensions over breaking changes.
- Publish compatibility table in release notes.

## Release gates

1. Automated plugin and TUI contract tests pass.
2. Manual smoke checklists pass.
3. Critical-path scenarios pass.
4. Documentation for changed workflows is updated.
