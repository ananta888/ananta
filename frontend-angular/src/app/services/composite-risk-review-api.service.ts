import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, map } from 'rxjs';

import { AgentDirectoryService } from './agent-directory.service';

export const COMPOSITE_RISK_REVIEW_WARNING =
  'Composite Risk Review ist nur ein optionaler Risiko-Hinweis. Ananta kann keine vollstaendige Absichtserkennung ueber beliebig zerlegte Aufgaben garantieren. Keine Warnung bedeutet nicht, dass ein Goal, eine Task-Kette oder ein Artefakt sicher ist.';

export interface CompositeRiskReviewRequest {
  explicit_request: true;
  goal?: unknown;
  tasks?: unknown[];
  artifacts_metadata?: unknown[];
  audit_events?: unknown[];
}

export interface CompositeRiskIndicator {
  id: string;
  description: string;
  severity: string;
  matched_evidence: Array<Record<string, unknown>>;
}

export interface CompositeRiskReviewResult {
  schema: 'composite_risk_review.v1';
  review_only: true;
  risk_level: 'insufficient_context' | 'low' | 'medium' | 'high';
  indicators: CompositeRiskIndicator[];
  explanation: string;
  recommended_action: string;
  warning_text: string;
}

interface ApiEnvelope<T> {
  data: T;
}

@Injectable({ providedIn: 'root' })
export class CompositeRiskReviewApiService {
  private readonly http = inject(HttpClient);
  private readonly directory = inject(AgentDirectoryService);

  review(payload: Record<string, unknown>): Observable<CompositeRiskReviewResult> {
    const hub = this.directory.list().find(agent => agent.role === 'hub');
    const origin = hub?.url ?? 'http://127.0.0.1:5000';
    return this.http
      .post<ApiEnvelope<CompositeRiskReviewResult>>(
        `${origin}/api/security/composite-risk-review`,
        { ...payload, explicit_request: true },
      )
      .pipe(map(response => response.data));
  }
}
