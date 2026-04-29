# Medusa Risk Checklist

Use this checklist before introducing any new integration/provider.

## Boundary checks

1. Can this provider be disabled without breaking core startup, core tests, or local-dev usage?
2. Does core consume only neutral artifacts/contracts, not provider-native payload structures?
3. Is provider-specific code isolated in adapter/implementation modules?
4. Does the integration bypass policy/approval/audit gates anywhere?

## Dependency checks

1. Are optional dependencies handled as degraded status instead of import-time failure?
2. Are secrets/credentials kept out of prompts, logs, and artifact payloads?
3. Is redaction applied to nested provider payloads before persistence?

## Runtime checks

1. Does provider execution require explicit Hub-issued capability/policy context?
2. Is dry-run mode available for risky operations?
3. Can unknown providers remain blocked/disabled by default?

## Artifact checks

1. Is provenance attached (provider_id/provider_family/provider_version/external_ref/source_ref/run_id/trace_id)?
2. Are required artifacts explicit and verifiable?
3. Are raw payload fields redacted and strictly optional?

## Release checks

1. Is the provider boundary checker clean for configured core modules?
2. Does smoke startup pass with optional providers disabled or missing?
3. Is this integration documented in provider plugin guide and relevant track todos?
