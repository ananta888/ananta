import {
  DELEGATED_SOURCE_MANIFEST_REF_V1_SCHEMA,
  DESTINATION_DESCRIPTOR_V1_SCHEMA,
  SOURCE_ACCESS_GRANT_V1_SCHEMA,
  SOURCE_CONNECTION_V1_SCHEMA,
  SOURCE_REF_MAPPING_V1_SCHEMA,
  SOURCE_REVISION_V1_SCHEMA,
  SourceControlRuntimeArtifact,
  SourceControlRuntimeValidationError,
  assertSourceControlArtifact,
  validateSourceControlArtifact,
} from './source-control-runtime-validators';

describe('versioned source-control runtime validators', () => {
  const hash = 'a'.repeat(64);
  const artifacts: SourceControlRuntimeArtifact[] = [
    {
      schema: SOURCE_CONNECTION_V1_SCHEMA,
      authority: 'hub',
      connection_id: `conn_${hash}`,
      tenant_id: 'tenant-a',
      project_id: 'project-a',
      owner_id: 'owner-a',
      connector_type: 'registered_workspace',
      connection_identity_digest: hash,
      display_name: 'Repository',
      sensitivity: 'internal',
      state: 'active',
      created_at: '2026-07-30T10:15:30Z',
    },
    {
      schema: SOURCE_REVISION_V1_SCHEMA,
      authority: 'hub',
      source_revision_id: `srev_${hash}`,
      connection_id: `conn_${hash}`,
      tenant_id: 'tenant-a',
      project_id: 'project-a',
      owner_id: 'owner-a',
      connector_type: 'git',
      sensitivity: 'internal_high',
      revision_token: 'refs/heads/main@abc123',
      revision_digest: hash,
      content_manifest_id: `manifest_${hash}`,
      content_manifest_digest: hash,
      admission_state: 'admitted',
      captured_at: '2026-07-30T10:15:30+02:00',
    },
    {
      schema: SOURCE_REF_MAPPING_V1_SCHEMA,
      authority: 'hub',
      source_ref_id: `sref_${hash}`,
      connection_id: `conn_${hash}`,
      source_revision_id: `srev_${hash}`,
      tenant_id: 'tenant-a',
      project_id: 'project-a',
      provenance_digest: hash,
    },
    {
      schema: DESTINATION_DESCRIPTOR_V1_SCHEMA,
      authority: 'hub',
      destination_id: `dst_${hash}`,
      worker_id: 'worker-a',
      worker_kind: 'native_ananta_worker',
      runtime_id: 'runtime-a',
      runtime_kind: 'docker_container',
      provider_id: 'provider-a',
      model_id: 'model-a',
      model_class: 'code',
      provider_location: 'private_network',
      data_residency: 'eu-central',
    },
    {
      schema: SOURCE_ACCESS_GRANT_V1_SCHEMA,
      authority: 'hub',
      grant_id: `grant_${hash}`,
      version: 1,
      tenant_id: 'tenant-a',
      project_id: 'project-a',
      source_revision_id: `srev_${hash}`,
      destination_id: `dst_${hash}`,
      operation: 'analyze',
      transformation: 'redacted',
      purpose: 'source.review',
      policy_version: 'policy-a:4',
      state: 'active',
      issued_at: '2026-07-30T08:00:00Z',
      expires_at: '2026-07-30T09:00:00Z',
    },
    {
      schema: DELEGATED_SOURCE_MANIFEST_REF_V1_SCHEMA,
      authority: 'hub',
      manifest_id: `manifest_${hash}`,
      manifest_digest: hash,
      source_revision_id: `srev_${hash}`,
      destination_id: `dst_${hash}`,
      source_access_grant_id: `grant_${hash}`,
      policy_version: 'policy-a:4',
    },
  ];

  it('accepts every schema-v1 artifact without adding or rewriting fields', () => {
    for (const artifact of artifacts) {
      const result = validateSourceControlArtifact(artifact);
      expect(result.valid).withContext(artifact.schema).toBeTruthy();
      if (result.valid) expect(result.value).toBe(artifact);
    }
  });

  it('enforces additionalProperties=false for every versioned artifact', () => {
    for (const artifact of artifacts) {
      const result = validateSourceControlArtifact({ ...artifact, local_default: true });
      expect(result.valid).withContext(artifact.schema).toBeFalsy();
      if (!result.valid) expect(result.issues.some((issue) => issue.code === 'additional_property')).toBeTruthy();
    }
  });

  it('does not fill missing required fields with local defaults', () => {
    const { authority: _authority, ...missingAuthority } = artifacts[0];
    const result = validateSourceControlArtifact(missingAuthority, SOURCE_CONNECTION_V1_SCHEMA);
    expect(result.valid).toBeFalsy();
    expect('authority' in missingAuthority).toBeFalsy();
    if (!result.valid) {
      expect(
        result.issues.some(
          (issue) => issue.path === '$.authority' && issue.code === 'required',
        ),
      ).toBeTruthy();
    }
  });

  it('rejects schema constants, identifier patterns and enum drift', () => {
    expect(validateSourceControlArtifact({ ...artifacts[0], authority: 'worker' }).valid).toBeFalsy();
    expect(validateSourceControlArtifact({ ...artifacts[1], source_revision_id: 'srev_short' }).valid).toBeFalsy();
    expect(validateSourceControlArtifact({ ...artifacts[3], provider_location: 'local' }).valid).toBeFalsy();
    expect(validateSourceControlArtifact({ ...artifacts[4], operation: 'locally_allowed' }).valid).toBeFalsy();
  });

  it('rejects malformed date-time values and non-positive grant versions', () => {
    expect(validateSourceControlArtifact({ ...artifacts[0], created_at: 'yesterday' }).valid).toBeFalsy();
    expect(validateSourceControlArtifact({ ...artifacts[0], created_at: '2026-02-30T10:15:30Z' }).valid).toBeFalsy();
    expect(validateSourceControlArtifact({ ...artifacts[0], created_at: '2025-02-29T10:15:30+02:00' }).valid).toBeFalsy();
    expect(validateSourceControlArtifact({ ...artifacts[0], created_at: '2024-02-29T10:15:30+02:00' }).valid).toBeTruthy();
    expect(validateSourceControlArtifact({ ...artifacts[4], version: 0 }).valid).toBeFalsy();
  });

  it('throws an unprocessable error only after returning all validation issues', () => {
    try {
      assertSourceControlArtifact(
        { ...artifacts[5], manifest_digest: 'not-a-digest', extra: true },
        DELEGATED_SOURCE_MANIFEST_REF_V1_SCHEMA,
      );
      fail('expected strict validator rejection');
    } catch (error) {
      expect(error instanceof SourceControlRuntimeValidationError).toBeTruthy();
      expect((error as SourceControlRuntimeValidationError).status).toBe(422);
      expect((error as SourceControlRuntimeValidationError).issues.length).toBe(2);
    }
  });
});
