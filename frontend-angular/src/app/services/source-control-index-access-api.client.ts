import { HttpClient, HttpHeaders, HttpParams, HttpResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, map } from 'rxjs';

import {
  SourceControlIndexAccessPreparation,
  SourceControlIndexAccessResult,
  parseSourceControlIndexAccessPreparation,
  parseSourceControlIndexAccessResult,
} from '../models/source-control-index-access.model';
import {
  assertSourceControlIdempotencyKey,
  assertSourceControlOpaqueId,
  parseSourceControlEnvelope,
} from '../models/source-control-v1-api.model';

const BASE_PATH = '/api/source-control/v1';

export interface SourceControlIndexAccessSelection {
  readonly destinationId: string;
  readonly optionId: string;
  readonly durationSeconds: number;
  readonly confirmed: true;
}

@Injectable({ providedIn: 'root' })
export class SourceControlIndexAccessApiClient {
  private readonly http = inject(HttpClient);

  prepare(
    connectionId: string,
    projectId: string,
  ): Observable<SourceControlIndexAccessPreparation> {
    const connection = this.pathId(connectionId, 'connection_id');
    assertSourceControlOpaqueId(projectId, 'project_id');
    return this.http.get<unknown>(
      `${BASE_PATH}/connections/${connection}/actions/prepare-index-access`,
      {
        params: new HttpParams().set('project_id', projectId),
        observe: 'response',
      },
    ).pipe(map(response => this.preparationResponse(response)));
  }

  grant(
    preparation: SourceControlIndexAccessPreparation,
    projectId: string,
    selection: SourceControlIndexAccessSelection,
    idempotencyKey: string,
  ): Observable<SourceControlIndexAccessResult> {
    assertSourceControlOpaqueId(projectId, 'project_id');
    assertSourceControlIdempotencyKey(idempotencyKey, 'idempotency_key');
    const destination = preparation.destinations.find(
      item => item.destination_id === selection.destinationId,
    );
    const option = preparation.options.find(item => item.option_id === selection.optionId);
    if (!preparation.readiness.ready || !destination || !option || selection.confirmed !== true) {
      throw new Error('index_access_selection_invalid');
    }
    if (
      !Number.isSafeInteger(selection.durationSeconds)
      || selection.durationSeconds < option.duration_seconds.minimum
      || selection.durationSeconds > option.duration_seconds.maximum
    ) {
      throw new Error('index_access_duration_invalid');
    }
    const connection = this.pathId(preparation.connection_id, 'connection_id');
    return this.http.post<unknown>(
      `${BASE_PATH}/connections/${connection}/actions/prepare-index-access`,
      {
        source_revision_id: preparation.source_revision.source_revision_id,
        destination_id: destination.destination_id,
        option_id: option.option_id,
        duration_seconds: selection.durationSeconds,
        confirmed: true,
      },
      {
        params: new HttpParams().set('project_id', projectId),
        headers: new HttpHeaders({
          'If-Match': `"${preparation.etag}"`,
          'Idempotency-Key': idempotencyKey,
        }),
        observe: 'response',
      },
    ).pipe(map(response => this.resultResponse(response)));
  }

  private preparationResponse(
    response: HttpResponse<unknown>,
  ): SourceControlIndexAccessPreparation {
    const preparation = parseSourceControlEnvelope(
      response.body,
      data => parseSourceControlIndexAccessPreparation(data),
    );
    const etag = strongEtag(response.headers.get('ETag'), 'index_access_etag');
    if (etag !== preparation.etag) throw new Error('index_access_etag_mismatch');
    return preparation;
  }

  private resultResponse(response: HttpResponse<unknown>): SourceControlIndexAccessResult {
    const result = parseSourceControlEnvelope(
      response.body,
      data => parseSourceControlIndexAccessResult(data),
    );
    const etag = strongEtag(response.headers.get('ETag'), 'index_access_grant_etag');
    if (etag !== result.grant.etag) throw new Error('index_access_grant_etag_mismatch');
    return result;
  }

  private pathId(value: string, path: string): string {
    assertSourceControlOpaqueId(value, path);
    return encodeURIComponent(value);
  }
}

function strongEtag(value: string | null, path: string): string {
  if (value === null || value.startsWith('W/')) throw new Error(`${path}_invalid`);
  const normalized = value.trim().replace(/^"|"$/g, '');
  if (!/^[0-9a-f]{64}$/.test(normalized)) throw new Error(`${path}_invalid`);
  return normalized;
}
