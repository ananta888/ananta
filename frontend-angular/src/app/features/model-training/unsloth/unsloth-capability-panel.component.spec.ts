import { describe, expect, it } from 'vitest';

import { TrainingCapabilities } from '../model-training.models';
import {
  buildUnslothMutationSummary,
  projectUnslothCapabilities,
} from './unsloth-capability-panel.component';

function capabilities(overrides: Partial<TrainingCapabilities> = {}): TrainingCapabilities {
  return {
    available: true,
    backends: [],
    gpu_profiles: [],
    base_models: [],
    limits: {},
    ...overrides,
  } as TrainingCapabilities;
}

describe('Unsloth capability projection', () => {
  it('keeps Core usable when optional profiles are not reported', () => {
    const projection = projectUnslothCapabilities(
      capabilities({
        unsloth: {
          core: { available: true, reason_code: 'core_ready' },
        },
      }),
    );

    expect(projection.core.available).toBe(true);
    expect(projection.core.reasonCode).toBe('core_ready');
    expect(projection.studio.available).toBe(false);
    expect(projection.studio.reasonCode).toBe('capability_not_reported_by_hub');
    expect(projection.mcp.available).toBe(false);
  });

  it('preserves Hub reason codes for release profiles and modalities', () => {
    const projection = projectUnslothCapabilities(
      capabilities({
        unsloth: {
          release_profile: {
            name: 'stable',
            available: false,
            reason_code: 'release_profile_not_installed',
          },
          modalities: {
            vision: { available: false, reason_code: 'vision_dependency_missing' },
          },
        },
      }),
    );

    expect(projection.releaseProfile.reasonCode).toBe('release_profile_not_installed');
    expect(projection.modalities.find((item) => item.key === 'vision')?.reasonCode).toBe(
      'vision_dependency_missing',
    );
  });

  it('projects executable compatibility facets and the cleanup operation', () => {
    const projection = projectUnslothCapabilities(
      capabilities({
        unsloth: {
          status: 'available',
          operations: {
            studio: { executable: false, reason_code: 'studio_transport_not_configured' },
            cleanup: { executable: true, reason_code: 'available' },
          },
        },
      }),
    );

    expect(projection.core.available).toBe(true);
    expect(projection.studio.reasonCode).toBe('studio_transport_not_configured');
    expect(projection.operations.cleanup.available).toBe(true);
  });
});

describe('Unsloth mutation summaries', () => {
  it('rejects paths and accepts only opaque resource identifiers', () => {
    const rejected = buildUnslothMutationSummary(
      'runtime_handoff',
      '/host/models/private',
      'Prepare runtime handoff',
    );
    const accepted = buildUnslothMutationSummary(
      'runtime_handoff',
      'adapter_01JABC',
      'Prepare runtime handoff',
      {
        promoted_artifact_id: 'lora-export-01JABC',
        promoted_artifact_sha256: 'a'.repeat(64),
        provider_descriptor: {
          provider_id: 'local-provider',
          provider_type: 'local-openai-compatible',
          model_id: 'local/model',
          provider_revision: 'revision-1',
          capabilities: {
            openai_chat: true,
            openai_responses: false,
            anthropic_messages: false,
            streaming: true,
            tools: true,
            structured_output: true,
          },
          limits: {
            timeout_seconds: 60,
            context_tokens: 8192,
            max_output_tokens: 2048,
            stream_idle_timeout_seconds: 30,
          },
        },
        endpoint_descriptor: {
          endpoint_id: 'endpoint-a',
          display_name: 'Local adapter',
          routing_key: 'adapter-a',
        },
        expected_endpoint_revision: 0,
        source_ids: ['SRC_supplied-model'],
        run_ids: ['RUN_supplied-evaluation'],
      },
    );

    expect(rejected).toEqual({
      summary: null,
      error: 'resource_id_must_be_opaque',
    });
    expect(JSON.stringify(accepted.summary)).not.toContain('/host/models/private');
    expect(accepted.summary).toMatchObject({
      transport: 'hub_only',
      dry_run: true,
      confirmed: false,
    });
  });

  it('requires selected opaque artifacts and a catalog revision for cleanup', () => {
    const rejected = buildUnslothMutationSummary(
      'cleanup',
      'tenant-storage',
      'Remove expired storage artifacts',
      null,
      { artifact_ids: [], expected_catalog_revision: 3 },
    );
    const accepted = buildUnslothMutationSummary(
      'cleanup',
      'tenant-storage',
      'Remove expired storage artifacts',
      null,
      { artifact_ids: ['artifact-storage-1'], expected_catalog_revision: 3 },
    );

    expect(rejected.error).toBe('storage_cleanup_contract_incomplete');
    expect(accepted.summary).toMatchObject({
      operation: 'cleanup',
      cleanup: {
        artifact_ids: ['artifact-storage-1'],
        expected_catalog_revision: 3,
      },
    });
  });
});
