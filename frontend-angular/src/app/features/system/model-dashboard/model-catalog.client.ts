import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import { HubApiCoreService } from '../../../services/hub-api-core.service';

export interface ModelSummary {
  readonly schema: 'ananta.model-summary.v1';
  readonly provider_id: string;
  readonly runtime: 'local' | 'cloud' | 'remote' | 'voice' | 'unknown';
  readonly model_id: string;
  readonly display_name: string;
  readonly availability: 'available' | 'degraded' | 'unavailable' | 'unknown';
  readonly loaded: boolean | null;
  readonly context_window: number | null;
  readonly quantization: string | null;
  readonly capabilities: readonly string[];
  readonly health: 'healthy' | 'degraded' | 'unavailable' | 'unknown';
  readonly is_default: boolean;
}

export interface ModelCatalog {
  readonly schema: 'ananta.model-catalog.v1';
  readonly default_selection: {
    readonly schema: 'ananta.model-default-selection.v1';
    readonly provider_id: string;
    readonly model_id: string;
  } | null;
  readonly models: readonly ModelSummary[];
  readonly provider_failures: readonly { provider_id: string; reason_code: string }[];
}

export type ModelRuntime = 'local' | 'cloud' | 'remote' | 'voice' | 'unknown';
export type ModelAvailability = 'available' | 'degraded' | 'unavailable' | 'unknown';

export interface ModelCapabilityClaim {
  readonly capability_id: string;
  readonly value: 'supported' | 'unsupported' | 'unknown';
  readonly evidence: 'declared' | 'detected' | 'benchmark' | 'manual' | 'unknown';
  readonly source_id: string | null;
}

export interface ModelMetadataFact {
  readonly fact_id: string;
  readonly value: string;
  readonly evidence: 'declared' | 'detected' | 'benchmark' | 'manual' | 'unknown';
  readonly source_id: string;
  readonly confidence: number | null;
}

export interface ModelInventoryDescriptor {
  readonly schema: 'ananta.model-inventory-descriptor.v2';
  readonly provider_id: string;
  readonly model_id: string;
  readonly executor_id: string;
  readonly display_name: string;
  readonly runtime: ModelRuntime;
  readonly source_ids: readonly string[];
  readonly source_kinds: readonly string[];
  readonly profile_ids: readonly string[];
  readonly aliases: readonly string[];
  readonly availability: ModelAvailability;
  readonly health: 'healthy' | 'degraded' | 'unavailable' | 'unknown';
  readonly configured: boolean;
  readonly installed: boolean | null;
  readonly loaded: boolean | null;
  readonly listing_supported: boolean;
  readonly auth_mode: string | null;
  readonly auth_ready: boolean | null;
  readonly context_window: number | null;
  readonly quantization: string | null;
  readonly input_modalities: readonly string[];
  readonly output_modalities: readonly string[];
  readonly price_input_per_million: number | null;
  readonly price_output_per_million: number | null;
  readonly capabilities: readonly ModelCapabilityClaim[];
  readonly metadata_facts: readonly ModelMetadataFact[];
  readonly conflicts: readonly string[];
  readonly used_by_consumers: readonly string[];
}

export interface ModelCatalogV2 {
  readonly schema: 'ananta.model-catalog.v2';
  readonly catalog_revision: number;
  readonly models: readonly ModelInventoryDescriptor[];
  readonly sources: readonly {
    source_id: string;
    source_kind: string;
    status: string;
    stale: boolean;
    from_cache: boolean;
    last_attempt_at: string | null;
    last_success_at: string | null;
    reason_code: string | null;
    model_count: number;
  }[];
  readonly partial: boolean;
}

export interface ModelConsumer {
  readonly schema: 'ananta.model-consumer.v1';
  readonly consumer_id: string;
  readonly label: string;
  readonly category: string;
  readonly required_capabilities: readonly string[];
  readonly allowed_scopes: readonly string[];
  readonly routable: boolean;
  readonly default_model_role: string;
  readonly legacy_config_paths: readonly string[];
  readonly mutation_capability: string;
  readonly registration_source: string;
  readonly non_routable_reason: string | null;
}

export interface ModelAssignment {
  consumer_id: string;
  scope: 'global' | 'organization' | 'project' | 'workflow' | 'agent' | 'role' | 'task_kind' | 'step';
  scope_id: string;
  mode: 'inherit' | 'profile' | 'model' | 'disabled';
  profile_id: string | null;
  provider_id: string | null;
  model_id: string | null;
  fallback_group_id: string | null;
}

export interface ModelFallbackCandidate {
  profile_id: string;
  retry_budget: number;
  triggers: string[];
  max_context_tokens: number | null;
  max_estimated_cost_per_step: number | null;
  requires_tools: boolean;
  requires_json: boolean;
  cloud_allowed: boolean;
}

export interface ModelFallbackGroup {
  group_id: string;
  candidates: ModelFallbackCandidate[];
  stop_on_policy_block: true;
  max_total_retries: number;
  on_exhausted: 'stop' | 'escalate';
  escalation_profile_id: string | null;
}

export interface ModelRoutingConfiguration {
  schema: 'ananta.model-routing-config.v1';
  revision: number;
  assignments: ModelAssignment[];
  fallback_groups: ModelFallbackGroup[];
}

export interface ModelRoutingValidationReport {
  readonly schema: 'ananta.model-routing-validation-report.v1';
  readonly valid: boolean;
  readonly expected_revision: number;
  readonly current_revision: number;
  readonly issues: readonly { severity: 'warning' | 'error'; reason_code: string; reference: string | null }[];
}

export interface ModelRoutingTemplate {
  readonly schema: 'ananta.model-routing-template.v1';
  readonly template_id: 'local-only' | 'local-first-cloud-fallback' | 'cloud-only' | 'cli-first';
  readonly label: string;
  readonly description: string;
  readonly applicable: boolean;
  readonly configuration: ModelRoutingConfiguration;
  readonly issues: readonly { severity: 'warning' | 'error'; reason_code: string; reference: string | null }[];
}

export interface ModelRoutingTemplateCatalog {
  readonly schema: 'ananta.model-routing-template-catalog.v1';
  readonly configuration_revision: number;
  readonly templates: readonly ModelRoutingTemplate[];
}

export interface EffectiveModelRoute {
  readonly schema: 'ananta.effective-model-route.v1';
  readonly configuration_revision: number;
  readonly consumer_id: string;
  readonly assignment_source: string;
  readonly inheritance_sources: readonly string[];
  readonly assignment_mode: 'inherit' | 'profile' | 'model' | 'disabled';
  readonly resolved_profile_id: string | null;
  readonly provider_id: string | null;
  readonly model_id: string | null;
  readonly fallback_group_id: string | null;
  readonly candidate_profile_ids: readonly string[];
  readonly blocked_candidates: readonly [string, string][];
  readonly decisions: readonly { rank: number; source: string; profile_id: string | null; accepted: boolean; reason: string }[];
  readonly maximum_total_retries: number | null;
  readonly executable: boolean;
}

export interface EffectiveModelRoutingProjection {
  readonly schema: 'ananta.effective-model-routing-projection.v1';
  readonly configuration_revision: number;
  readonly routes: readonly EffectiveModelRoute[];
}

function unwrap<T>(value: unknown): T {
  return (value && typeof value === 'object' && 'data' in value
    ? (value as { data: unknown }).data
    : value) as T;
}

export function canUseModelMutation(
  user: unknown,
  capability: 'model_catalog.refresh' | 'model_catalog.set_default' | 'model_routing.read'
    | 'model_routing.validate' | 'model_routing.export' | 'model_routing.mutate',
): boolean {
  if (!user || typeof user !== 'object') return false;
  const payload = user as Record<string, unknown>;
  if (payload['auth_mode'] === 'auth_disabled') return false;
  if (payload['role'] === 'admin') return true;
  const capabilities = Array.isArray(payload['capabilities']) ? payload['capabilities'] : [];
  return capabilities.includes(capability);
}

@Injectable({ providedIn: 'root' })
export class ModelCatalogClient {
  private readonly api = inject(HubApiCoreService);

  read(baseUrl: string): Observable<ModelCatalog> {
    return this.api.get<unknown>(
      `${baseUrl.replace(/\/$/, '')}/models/catalog/v1`,
      baseUrl,
      undefined,
      false,
    ).pipe(map(unwrap<ModelCatalog>));
  }

  refresh(baseUrl: string): Observable<ModelCatalog> {
    return this.api.post<unknown>(
      `${baseUrl.replace(/\/$/, '')}/models/catalog/v1/refresh`,
      {},
      baseUrl,
    ).pipe(map(unwrap<ModelCatalog>));
  }

  readInventory(baseUrl: string): Observable<ModelCatalogV2> {
    return this.api.get<unknown>(
      `${baseUrl.replace(/\/$/, '')}/models/catalog/v2`, baseUrl, undefined, false,
    ).pipe(map(unwrap<ModelCatalogV2>));
  }

  refreshInventory(baseUrl: string): Observable<ModelCatalogV2> {
    return this.api.post<unknown>(
      `${baseUrl.replace(/\/$/, '')}/models/catalog/v2/refresh`, {}, baseUrl,
    ).pipe(map(unwrap<ModelCatalogV2>));
  }

  readConsumers(baseUrl: string): Observable<{ schema: string; consumers: readonly ModelConsumer[] }> {
    return this.api.get<unknown>(
      `${baseUrl.replace(/\/$/, '')}/models/consumers/v1`, baseUrl, undefined, false,
    ).pipe(map(unwrap<{ schema: string; consumers: readonly ModelConsumer[] }>));
  }

  readRouting(baseUrl: string): Observable<ModelRoutingConfiguration> {
    return this.api.get<unknown>(
      `${baseUrl.replace(/\/$/, '')}/models/routing/v1`, baseUrl, undefined, false,
    ).pipe(map(unwrap<ModelRoutingConfiguration>));
  }

  readEffectiveRouting(baseUrl: string): Observable<EffectiveModelRoutingProjection> {
    return this.api.get<unknown>(
      `${baseUrl.replace(/\/$/, '')}/models/routing/v1/effective`, baseUrl, undefined, false,
    ).pipe(map(unwrap<EffectiveModelRoutingProjection>));
  }

  readRoutingTemplates(baseUrl: string): Observable<ModelRoutingTemplateCatalog> {
    return this.api.get<unknown>(
      `${baseUrl.replace(/\/$/, '')}/models/routing/v1/templates`, baseUrl, undefined, false,
    ).pipe(map(unwrap<ModelRoutingTemplateCatalog>));
  }

  validateRouting(baseUrl: string, value: ModelRoutingConfiguration): Observable<ModelRoutingValidationReport> {
    return this.api.post<unknown>(
      `${baseUrl.replace(/\/$/, '')}/models/routing/v1/validate`,
      this.mutation(value), baseUrl,
    ).pipe(map(unwrap<ModelRoutingValidationReport>));
  }

  saveRouting(baseUrl: string, value: ModelRoutingConfiguration): Observable<ModelRoutingConfiguration> {
    return this.api.put<unknown>(
      `${baseUrl.replace(/\/$/, '')}/models/routing/v1`, this.mutation(value), baseUrl,
    ).pipe(map(unwrap<ModelRoutingConfiguration>));
  }

  dryRun(
    baseUrl: string,
    consumerId: string,
    configuration?: ModelRoutingConfiguration,
  ): Observable<EffectiveModelRoute> {
    return this.api.post<unknown>(
      `${baseUrl.replace(/\/$/, '')}/models/routing/v1/dry-run`,
      {
        schema: 'ananta.model-routing-dry-run-command.v1',
        consumer_id: consumerId,
        ...(configuration ? { configuration } : {}),
      },
      baseUrl,
    ).pipe(map(unwrap<EffectiveModelRoute>));
  }

  exportRouting(baseUrl: string): Observable<{ schema: string; configuration: ModelRoutingConfiguration }> {
    return this.api.get<unknown>(
      `${baseUrl.replace(/\/$/, '')}/models/routing/v1/export`, baseUrl, undefined, false,
    ).pipe(map(unwrap<{ schema: string; configuration: ModelRoutingConfiguration }>));
  }

  previewImport(baseUrl: string, value: ModelRoutingConfiguration, expectedRevision: number): Observable<Record<string, unknown>> {
    return this.api.post<unknown>(
      `${baseUrl.replace(/\/$/, '')}/models/routing/v1/import/preview`,
      { schema: 'ananta.model-routing-import-command.v1', expected_revision: expectedRevision, configuration: value },
      baseUrl,
    ).pipe(map(unwrap<Record<string, unknown>>));
  }

  applyImport(
    baseUrl: string,
    value: ModelRoutingConfiguration,
    expectedRevision: number,
    confirmationDigest: string,
  ): Observable<ModelRoutingConfiguration> {
    return this.api.post<unknown>(
      `${baseUrl.replace(/\/$/, '')}/models/routing/v1/import/apply`,
      {
        schema: 'ananta.model-routing-import-command.v1',
        expected_revision: expectedRevision,
        configuration: value,
        confirmation_digest: confirmationDigest,
      },
      baseUrl,
    ).pipe(map(unwrap<ModelRoutingConfiguration>));
  }

  private mutation(value: ModelRoutingConfiguration): Record<string, unknown> {
    return {
      schema: 'ananta.model-routing-mutation-command.v1',
      expected_revision: value.revision,
      assignments: value.assignments,
      fallback_groups: value.fallback_groups,
    };
  }

  selectDefault(baseUrl: string, providerId: string, modelId: string): Observable<unknown> {
    return this.api.post(
      `${baseUrl.replace(/\/$/, '')}/models/default/v1`,
      {
        schema: 'ananta.model-default-selection-command.v1',
        provider_id: providerId,
        model_id: modelId,
      },
      baseUrl,
    );
  }
}
