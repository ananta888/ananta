import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { ApiBaseService } from '../../services/api-base.service';
import {
  RuntimeOperationCommandRequest,
  RuntimeOperationCommandResponse,
  RuntimeCapabilityMatrixProjection,
  RuntimeOperationsFilters,
  RuntimeOperationsResponse,
} from './workflow-runtime-operations.models';

@Injectable({ providedIn: 'root' })
export class WorkflowRuntimeOperationsApiService extends ApiBaseService {
  capabilities(
    hubUrl: string,
    requiredCapabilities: string[] = [],
    token?: string,
  ): Observable<RuntimeCapabilityMatrixProjection> {
    const query = new URLSearchParams();
    for (const capability of [...new Set(requiredCapabilities.map(value => value.trim()).filter(Boolean))].sort()) {
      query.append('required_capability', capability);
    }
    const suffix = query.size ? `?${query.toString()}` : '';
    return this.core.get<RuntimeCapabilityMatrixProjection>(
      `${hubUrl}/api/workflow-runtime/capabilities${suffix}`,
      hubUrl,
      token,
      false,
    );
  }

  list(
    hubUrl: string,
    filters: RuntimeOperationsFilters,
    token?: string,
  ): Observable<RuntimeOperationsResponse> {
    const query = new URLSearchParams();
    if (filters.runtime) query.set('runtime', filters.runtime);
    if (filters.mode) query.set('mode', filters.mode);
    if (filters.status) query.set('status', filters.status);
    if (filters.health) query.set('health', filters.health);
    if (filters.q.trim()) query.set('q', filters.q.trim());
    query.set('limit', '200');
    return this.core.get<RuntimeOperationsResponse>(
      `${hubUrl}/api/workflow-runtime/operations?${query.toString()}`,
      hubUrl,
      token,
      false,
    );
  }

  command(
    hubUrl: string,
    runId: string,
    command: RuntimeOperationCommandRequest,
    idempotencyKey: string,
    token?: string,
  ): Observable<RuntimeOperationCommandResponse> {
    return this.core.request<RuntimeOperationCommandResponse>(
      'POST',
      `${hubUrl}/api/workflow-runtime/operations/runs/${encodeURIComponent(runId)}/commands`,
      hubUrl,
      {
        body: command,
        token,
        headers: { 'Idempotency-Key': idempotencyKey },
      },
    );
  }
}
