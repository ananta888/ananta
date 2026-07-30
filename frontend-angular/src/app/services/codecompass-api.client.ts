import { Injectable, inject } from '@angular/core';
import { Observable, catchError, map, throwError } from 'rxjs';

import {
  CodeCompassGraphV1,
  CodeCompassLifecycleCapabilitiesV1,
  CodeCompassQueryResultV1,
  CodeCompassQueryV1,
  KnowledgeIndexV1,
  normalizeCodeCompassGraph,
  normalizeCodeCompassQueryResult,
  normalizeContractId,
  normalizeKnowledgeIndexList,
  toSourceControlApiError,
} from '../models/source-control-contracts';
import { HubApiCoreService } from './hub-api-core.service';

export interface CodeCompassReadPort {
  listIndexes(hubUrl: string, sourceScope?: string): Observable<readonly KnowledgeIndexV1[]>;
  getGraph(hubUrl: string, knowledgeIndexId: string): Observable<CodeCompassGraphV1>;
  query(hubUrl: string, request: CodeCompassQueryV1): Observable<CodeCompassQueryResultV1>;
}

export interface CodeCompassLifecyclePort {
  capabilities(): CodeCompassLifecycleCapabilitiesV1;
}

@Injectable({ providedIn: 'root' })
export class CodeCompassReadClient implements CodeCompassReadPort {
  private readonly core = inject(HubApiCoreService);

  listIndexes(hubUrl: string, sourceScope = ''): Observable<readonly KnowledgeIndexV1[]> {
    const query = new URLSearchParams({ limit: '500' });
    if (sourceScope) query.set('source_scope', sourceScope);
    return this.core.get<unknown>(
      `${cleanHubUrl(hubUrl)}/knowledge/indices?${query.toString()}`,
      hubUrl,
      undefined,
      false,
    ).pipe(
      map(normalizeKnowledgeIndexList),
      catchError(error => throwError(() => toSourceControlApiError(error, 'knowledge_indices_load'))),
    );
  }

  getGraph(hubUrl: string, knowledgeIndexId: string): Observable<CodeCompassGraphV1> {
    const indexId = normalizeContractId(knowledgeIndexId, 'knowledge_index_id_invalid');
    const query = new URLSearchParams({ knowledge_index_id: indexId });
    return this.core.get<unknown>(
      `${cleanHubUrl(hubUrl)}/api/codecompass/graph?${query.toString()}`,
      hubUrl,
      undefined,
      false,
    ).pipe(
      map(value => normalizeCodeCompassGraph(value, indexId)),
      catchError(error => throwError(() => toSourceControlApiError(error, 'codecompass_graph_load'))),
    );
  }

  query(hubUrl: string, request: CodeCompassQueryV1): Observable<CodeCompassQueryResultV1> {
    const indexId = normalizeContractId(
      request.knowledge_index_id,
      'knowledge_index_id_invalid',
    );
    const queryType = boundedQueryValue(request.query_type, 'query_type_invalid');
    const seed = boundedQueryValue(request.seed, 'query_seed_invalid');
    const query = new URLSearchParams({
      knowledge_index_id: indexId,
      type: queryType,
      seed,
    });
    if (request.field) query.set('field', boundedQueryValue(request.field, 'query_field_invalid'));
    if (request.depth !== undefined) query.set('depth', String(Math.max(0, Math.floor(request.depth))));
    if (request.direction) query.set('direction', request.direction);
    return this.core.get<unknown>(
      `${cleanHubUrl(hubUrl)}/api/codecompass/query?${query.toString()}`,
      hubUrl,
      undefined,
      false,
    ).pipe(
      map(value => normalizeCodeCompassQueryResult(value, indexId)),
      catchError(error => throwError(() => toSourceControlApiError(error, 'codecompass_query'))),
    );
  }
}

@Injectable({ providedIn: 'root' })
export class CodeCompassLifecycleClient implements CodeCompassLifecyclePort {
  capabilities(): CodeCompassLifecycleCapabilitiesV1 {
    return {
      schema: 'codecompass_lifecycle_capabilities.v1',
      reindex: false,
      activate: false,
      rollback: false,
      cleanup: false,
      reason_code: 'codecompass_lifecycle_routes_unavailable',
    };
  }
}

function cleanHubUrl(value: string): string {
  return String(value || '').replace(/\/+$/, '');
}

function boundedQueryValue(value: unknown, reasonCode: string): string {
  const candidate = String(value || '').trim().slice(0, 512);
  if (!candidate || /[\u0000-\u001f]/.test(candidate)) {
    throw toSourceControlApiError(
      {
        status: 422,
        error: { data: { reason_code: reasonCode } },
      },
      'codecompass_query',
    );
  }
  return candidate;
}
