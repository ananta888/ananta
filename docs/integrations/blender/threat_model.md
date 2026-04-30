# Blender Threat Model

## Main threats
- Prompt/script injection
- Unsafe local file/network actions
- Scene data exfiltration
- Accidental destructive mutations

## Controls
- Hub policy + approval ownership
- Capability-tagged actions
- Default-deny script execution
- Bounded context payload with provenance
- Audit trails with correlation and hashes
