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

function unwrap<T>(value: unknown): T {
  return (value && typeof value === 'object' && 'data' in value
    ? (value as { data: unknown }).data
    : value) as T;
}

export function canUseModelMutation(
  user: unknown,
  capability: 'model_catalog.refresh' | 'model_catalog.set_default',
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

