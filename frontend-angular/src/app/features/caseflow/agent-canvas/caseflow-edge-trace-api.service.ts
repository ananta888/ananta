import { HttpClient, HttpContext } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, defer, map } from 'rxjs';

import {
  AgentDirectoryService,
  normalizeHubOrigin,
} from '../../../services/agent-directory.service';
import { SUPPRESS_GLOBAL_ERROR_NOTIFICATION } from '../../../services/error-request-context';
import {
  CASEFLOW_EDGE_TRACE_QUERY_SCHEMA,
  CaseFlowEdgeTraceReadModel,
  CaseFlowEdgeTraceScope,
} from './caseflow-edge-trace.models';
import {
  decodeCaseFlowEdgeTraceReadModel,
  validateCaseFlowEdgeTraceScope,
} from './caseflow-edge-trace.validator';

/** Read-only adapter for the Hub-owned CaseFlow edge projection. */
@Injectable({ providedIn: 'root' })
export class CaseFlowEdgeTraceApiService {
  private readonly http = inject(HttpClient);
  private readonly directory = inject(AgentDirectoryService);

  read(scope: Readonly<CaseFlowEdgeTraceScope>): Observable<CaseFlowEdgeTraceReadModel> {
    return defer(() => {
      const canonicalScope = validateCaseFlowEdgeTraceScope(scope);
      const hubOrigin = this.hubOrigin();
      const workflowPath = encodeURIComponent(canonicalScope.workflow_id);
      return this.http.post<unknown>(
        `${hubOrigin}/api/visual-process/workflow/${workflowPath}/caseflow-edge-trace`,
        {
          schema: CASEFLOW_EDGE_TRACE_QUERY_SCHEMA,
          run_id: canonicalScope.run_id,
        },
        {
          context: new HttpContext().set(SUPPRESS_GLOBAL_ERROR_NOTIFICATION, true),
        },
      ).pipe(
        map(response => decodeCaseFlowEdgeTraceReadModel(response, canonicalScope)),
      );
    });
  }

  private hubOrigin(): string {
    const configured = this.directory.list().find(agent => agent.role === 'hub')?.url ?? '';
    const origin = normalizeHubOrigin(configured);
    if (!origin) throw new Error('caseflow_hub_origin_unavailable');
    return origin;
  }
}
