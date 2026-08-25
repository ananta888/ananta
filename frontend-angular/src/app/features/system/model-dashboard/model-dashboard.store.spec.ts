import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { describe, expect, it, beforeEach, vi } from 'vitest';

import { NotificationService } from '../../../services/notification.service';
import { UserAuthService } from '../../../services/user-auth.service';
import { SystemFacade } from '../../system/system.facade';
import { ModelCatalogClient, ModelRoutingConfiguration } from './model-catalog.client';
import { ModelDashboardStore } from './model-dashboard.store';

const routing = (): ModelRoutingConfiguration => ({
  schema: 'ananta.model-routing-config.v1', revision: 4, assignments: [], fallback_groups: [],
});

describe('ModelDashboardStore', () => {
  let client: Record<string, ReturnType<typeof vi.fn>>;
  let store: ModelDashboardStore;

  beforeEach(() => {
    const models = Array.from({ length: 5000 }, (_, index) => ({
      schema: 'ananta.model-inventory-descriptor.v2', provider_id: 'local',
      model_id: `model-${index}`, executor_id: 'api:local', display_name: `Model ${index}`,
      runtime: 'local', source_ids: ['providers.catalog'], source_kinds: ['discovered'],
      profile_ids: [`profile-${index}`], aliases: [], availability: 'available', health: 'healthy',
      configured: true, installed: true, loaded: false, listing_supported: true,
      auth_mode: null, auth_ready: null, context_window: 8192, quantization: null,
      input_modalities: ['text'], output_modalities: ['text'],
      price_input_per_million: null, price_output_per_million: null,
      capabilities: [], metadata_facts: [], conflicts: [], used_by_consumers: [],
    }));
    client = {
      readInventory: vi.fn(() => of({
        schema: 'ananta.model-catalog.v2', catalog_revision: 1, models, sources: [], partial: false,
      })),
      read: vi.fn(() => of({
        schema: 'ananta.model-catalog.v1', default_selection: null, models: [], provider_failures: [],
      })),
      readConsumers: vi.fn(() => of({
        schema: 'ananta.model-consumer-registry.v1', consumers: [{
          schema: 'ananta.model-consumer.v1', consumer_id: 'task.coding', label: 'Coding',
          category: 'tasks', required_capabilities: ['code'], allowed_scopes: ['global'], routable: true,
          default_model_role: 'coder', legacy_config_paths: [],
          mutation_capability: 'model_routing.mutate', registration_source: 'builtin',
          non_routable_reason: null,
        }],
      })),
      readRouting: vi.fn(() => of(routing())),
      readRoutingTemplates: vi.fn(() => of({
        schema: 'ananta.model-routing-template-catalog.v1', configuration_revision: 4,
        templates: [{
          schema: 'ananta.model-routing-template.v1', template_id: 'local-only',
          label: 'Nur lokal', description: 'Lokal', applicable: true, issues: [],
          configuration: {
            ...routing(), assignments: [{
              consumer_id: 'task.coding', scope: 'global', scope_id: 'global', mode: 'profile',
              profile_id: 'profile-1', provider_id: null, model_id: null, fallback_group_id: null,
            }],
          },
        }],
      })),
      refreshInventory: vi.fn(), selectDefault: vi.fn(), dryRun: vi.fn(),
      exportRouting: vi.fn(), previewImport: vi.fn(), applyImport: vi.fn(),
      validateRouting: vi.fn(() => of({
        schema: 'ananta.model-routing-validation-report.v1', valid: true,
        expected_revision: 4, current_revision: 4, issues: [],
      })),
      saveRouting: vi.fn(() => of({ ...routing(), revision: 5 })),
    };
    TestBed.configureTestingModule({ providers: [
      ModelDashboardStore,
      { provide: ModelCatalogClient, useValue: client },
      { provide: SystemFacade, useValue: { resolveHubAgent: () => ({ url: 'http://hub' }) } },
      { provide: UserAuthService, useValue: {
        userPayload: { role: 'admin' }, user$: of({ role: 'admin' }),
      } },
      { provide: NotificationService, useValue: { success: vi.fn() } },
    ] });
    store = TestBed.inject(ModelDashboardStore);
    store.load();
  });

  it('filters 5000 models but bounds simultaneous rendering', () => {
    expect(store.filteredModels()).toHaveLength(5000);
    expect(store.visibleModels()).toHaveLength(500);
    store.search.set('model-4999');
    expect(store.filteredModels().map(model => model.model_id)).toEqual(['model-4999']);
  });

  it('keeps assignment and fallback edits local until reset or save', () => {
    store.setConsumerMode('task.coding', 'profile', 'profile-1');
    store.addFallbackGroup('local-code');
    store.addCandidate('local-code', 'profile-1');
    store.addCandidate('local-code', 'profile-2');
    store.moveCandidate('local-code', 1, -1);

    expect(store.dirty()).toBe(true);
    expect(store.authoritativeRouting()?.assignments).toEqual([]);
    expect(store.draftRouting()?.fallback_groups[0].candidates.map(item => item.profile_id))
      .toEqual(['profile-2', 'profile-1']);
    store.resetDraft();
    expect(store.dirty()).toBe(false);
  });

  it('edits fallback conditions and limits only in the local draft', () => {
    store.addFallbackGroup('guarded-code');
    store.addCandidate('guarded-code', 'profile-1');
    store.setCandidateTriggers('guarded-code', 0, 'timeout, connection_error, timeout');
    store.setCandidateLimit('guarded-code', 0, 'max_context_tokens', 32768);
    store.setCandidateLimit('guarded-code', 0, 'max_estimated_cost_per_step', 0.25);
    store.setCandidateRequirement('guarded-code', 0, 'requires_tools', true);
    store.setCandidateRequirement('guarded-code', 0, 'cloud_allowed', true);
    store.setGroupExhaustion('guarded-code', 'escalate', 'profile-2');

    const group = store.draftRouting()?.fallback_groups[0];
    expect(group?.candidates[0]).toMatchObject({
      triggers: ['timeout', 'connection_error'], max_context_tokens: 32768,
      max_estimated_cost_per_step: 0.25, requires_tools: true, cloud_allowed: true,
    });
    expect(group).toMatchObject({ on_exhausted: 'escalate', escalation_profile_id: 'profile-2' });
    expect(store.authoritativeRouting()?.fallback_groups).toEqual([]);
  });

  it('applies a safe template only to the draft at the authoritative revision', () => {
    store.applyTemplate('local-only');

    expect(store.draftRouting()?.revision).toBe(4);
    expect(store.draftRouting()?.assignments[0].profile_id).toBe('profile-1');
    expect(store.authoritativeRouting()?.assignments).toEqual([]);
    expect(store.dirty()).toBe(true);
  });

  it('validates before one atomic save and adopts the authoritative revision', () => {
    store.setConsumerMode('task.coding', 'profile', 'profile-1');
    store.save();

    expect(client['validateRouting']).toHaveBeenCalledTimes(1);
    expect(client['saveRouting']).toHaveBeenCalledTimes(1);
    expect(store.authoritativeRouting()?.revision).toBe(5);
    expect(store.dirty()).toBe(false);
  });

  it('does not save when server validation fails', () => {
    client['validateRouting'].mockReturnValue(throwError(() => new Error('invalid')));
    store.setConsumerMode('task.coding', 'profile', 'profile-1');
    store.save();
    expect(client['saveRouting']).not.toHaveBeenCalled();
  });
});
