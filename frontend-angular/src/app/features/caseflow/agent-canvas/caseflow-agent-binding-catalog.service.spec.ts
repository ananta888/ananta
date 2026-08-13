import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of, throwError } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { HubTasksApiClient } from '../../../services/hub-tasks-api.client';
import {
  CASEFLOW_CONTEXT_SOURCE_CATALOG,
  CASEFLOW_INSTRUCTION_PROFILE_CATALOG,
  CASEFLOW_MODEL_ROUTING_CATALOG,
  CASEFLOW_SKILL_PROFILE_CATALOG,
  CaseFlowAgentBindingCatalogService,
  CaseFlowInstructionProfileCatalogAdapter,
} from './caseflow-agent-binding-catalog.service';

const INSTRUCTION_LIVE_ID = '5d5ef0c1-6248-46f4-8733-97edcd6bf1c4';
const INSTRUCTION_STALE_ID = '25ff8823-62a1-461c-8512-60a6a906b233';

describe('CaseFlowAgentBindingCatalogService', () => {
  const skills = { listSkillProfiles: vi.fn() };
  const instructions = { listInstructionProfiles: vi.fn() };
  const contexts = { listSources: vi.fn() };
  const models = { listModelProfiles: vi.fn() };

  beforeEach(() => {
    vi.clearAllMocks();
    skills.listSkillProfiles.mockReturnValue(of([]));
    instructions.listInstructionProfiles.mockReturnValue(of([]));
    contexts.listSources.mockReturnValue(of([]));
    models.listModelProfiles.mockReturnValue(of(loadedModels()));
    TestBed.configureTestingModule({
      providers: [
        CaseFlowAgentBindingCatalogService,
        { provide: CASEFLOW_SKILL_PROFILE_CATALOG, useValue: skills },
        { provide: CASEFLOW_INSTRUCTION_PROFILE_CATALOG, useValue: instructions },
        { provide: CASEFLOW_CONTEXT_SOURCE_CATALOG, useValue: contexts },
        { provide: CASEFLOW_MODEL_ROUTING_CATALOG, useValue: models },
      ],
    });
  });

  it('projects only active IDs, deduplicates them and drops sensitive source fields', async () => {
    skills.listSkillProfiles.mockReturnValue(of([
      { id: 'skill-b', description: 'internal details' },
      { id: 'quality profile' },
      { id: 'skill-b' },
      { id: 'skill-disabled', enabled: false },
    ]));
    instructions.listInstructionProfiles.mockReturnValue(of([
      {
        id: INSTRUCTION_LIVE_ID,
        is_active: true,
        owner_username: 'tenant-secret',
        prompt_content: 'never expose this prompt',
      },
      { id: INSTRUCTION_LIVE_ID, is_active: true },
      { id: 'instruction-inactive', is_active: false },
      { id: 'instruction-without-state' },
    ]));
    contexts.listSources.mockReturnValue(of([
      {
        source_id: 'source-live',
        enabled: true,
        fetch_source: { token: 'source-secret' },
      },
      { source_id: 'source-live', enabled: true },
      { source_id: 'source-disabled', enabled: false },
    ]));
    models.listModelProfiles.mockReturnValue(of(loadedModels({
      profiles: [
        {
          profile_id: 'model-b',
          model_role: 'review',
          provider_id: 'private-provider',
          api_key_env: 'PRIVATE_KEY',
        },
        { profile_id: 'model premium', model_role: 'code' },
        { profile_id: 'model-b', model_role: 'review' },
        { profile_id: 'model-disabled', model_role: 'secret-role', enabled: false },
      ],
      fallback_groups: {
        'fallback-b': { ordered_profiles: ['model-b'] },
        'fallback primary': { ordered_profiles: ['model premium'] },
      },
    })));

    const readModel = await firstValueFrom(
      TestBed.inject(CaseFlowAgentBindingCatalogService).load(),
    );

    expect(readModel.state).toBe('ready');
    expect(readModel.catalog).toEqual({
      skill_profile_ids: ['quality profile', 'skill-b'],
      personality_resource_ids: {
        agent_profile: [],
        instruction_layer: [INSTRUCTION_LIVE_ID],
      },
      context_resource_ids: {
        context_profile: [],
        context_source: ['source-live'],
      },
      model_profile_ids: ['model premium', 'model-b'],
      model_role_ids: ['code', 'review'],
      fallback_group_ids: ['fallback primary', 'fallback-b'],
    });
    expect(readModel.availability.agent_profile).toEqual({
      state: 'disabled',
      reason_code: 'catalog_not_implemented',
    });
    expect(readModel.availability.context_profile).toEqual({
      state: 'disabled',
      reason_code: 'catalog_not_implemented',
    });

    const serialized = JSON.stringify(readModel);
    expect(serialized).not.toContain('tenant-secret');
    expect(serialized).not.toContain('never expose this prompt');
    expect(serialized).not.toContain('source-secret');
    expect(serialized).not.toContain('PRIVATE_KEY');
    expect(serialized).not.toContain('private-provider');
  });

  it('keeps successful resources ready while independent errors fail closed', async () => {
    skills.listSkillProfiles.mockReturnValue(throwError(() => ({ status: 403 })));
    instructions.listInstructionProfiles.mockReturnValue(of([
      { id: INSTRUCTION_LIVE_ID, is_active: true },
    ]));
    contexts.listSources.mockReturnValue(throwError(() => ({ status: 500 })));
    models.listModelProfiles.mockReturnValue(of({
      status: 'error',
      profiles: [{ profile_id: 'must-not-leak', model_role: 'must-not-leak' }],
      fallback_groups: { 'must-not-leak': {} },
    }));

    const readModel = await firstValueFrom(
      TestBed.inject(CaseFlowAgentBindingCatalogService).load(),
    );

    expect(readModel.state).toBe('degraded');
    expect(readModel.catalog.skill_profile_ids).toEqual([]);
    expect(readModel.catalog.personality_resource_ids.instruction_layer).toEqual([
      INSTRUCTION_LIVE_ID,
    ]);
    expect(readModel.catalog.context_resource_ids.context_source).toEqual([]);
    expect(readModel.catalog.model_profile_ids).toEqual([]);
    expect(readModel.catalog.model_role_ids).toEqual([]);
    expect(readModel.catalog.fallback_group_ids).toEqual([]);
    expect(readModel.availability.skill_profile.reason_code).toBe(
      'catalog_request_forbidden',
    );
    expect(readModel.availability.context_source.reason_code).toBe(
      'catalog_request_failed',
    );
    expect(readModel.availability.model_profile.reason_code).toBe(
      'catalog_response_invalid',
    );
  });

  it('does not retain stale instruction IDs after a later 403', async () => {
    instructions.listInstructionProfiles
      .mockReturnValueOnce(of([{ id: INSTRUCTION_STALE_ID, is_active: true }]))
      .mockReturnValueOnce(throwError(() => ({ status: 403 })));
    const service = TestBed.inject(CaseFlowAgentBindingCatalogService);

    const first = await firstValueFrom(service.load());
    const second = await firstValueFrom(service.load());

    expect(first.catalog.personality_resource_ids.instruction_layer).toEqual([
      INSTRUCTION_STALE_ID,
    ]);
    expect(second.catalog.personality_resource_ids.instruction_layer).toEqual([]);
    expect(second.availability.instruction_layer).toEqual({
      state: 'degraded',
      reason_code: 'catalog_request_forbidden',
    });
  });

  it('rejects a partly malformed response instead of accepting its valid IDs', async () => {
    skills.listSkillProfiles.mockReturnValue(of([
      { id: 'apparently-valid' },
      { id: ' invalid-id' },
    ]));

    const readModel = await firstValueFrom(
      TestBed.inject(CaseFlowAgentBindingCatalogService).load(),
    );

    expect(readModel.catalog.skill_profile_ids).toEqual([]);
    expect(readModel.availability.skill_profile).toEqual({
      state: 'degraded',
      reason_code: 'catalog_response_invalid',
    });
  });

  it('marks an explicitly unconfigured model subsystem disabled without inventing IDs', async () => {
    models.listModelProfiles.mockReturnValue(of({
      status: 'not_configured',
      profiles: [],
      fallback_groups: {},
    }));

    const readModel = await firstValueFrom(
      TestBed.inject(CaseFlowAgentBindingCatalogService).load(),
    );

    expect(readModel.state).toBe('ready');
    expect(readModel.catalog.model_profile_ids).toEqual([]);
    expect(readModel.catalog.model_role_ids).toEqual([]);
    expect(readModel.catalog.fallback_group_ids).toEqual([]);
    expect(readModel.availability.model_profile).toEqual({
      state: 'disabled',
      reason_code: 'catalog_not_configured',
    });
  });
});

describe('CaseFlowInstructionProfileCatalogAdapter', () => {
  it('uses the authenticated Hub scope without an owner_username override', async () => {
    const client = { listInstructionProfiles: vi.fn(() => of([])) };
    TestBed.configureTestingModule({
      providers: [
        CaseFlowInstructionProfileCatalogAdapter,
        { provide: HubTasksApiClient, useValue: client },
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ name: 'hub', role: 'hub', url: 'https://hub.test/' }] },
        },
      ],
    });

    await firstValueFrom(
      TestBed.inject(CaseFlowInstructionProfileCatalogAdapter).listInstructionProfiles(),
    );

    expect(client.listInstructionProfiles).toHaveBeenCalledWith('https://hub.test');
    expect(client.listInstructionProfiles.mock.calls[0]).toHaveLength(1);
  });
});

function loadedModels(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    status: 'loaded',
    profiles: [],
    fallback_groups: {},
    ...overrides,
  };
}
