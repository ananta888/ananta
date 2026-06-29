import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  CaseFlowCase,
  CaseEvent,
  CaseArtifact,
  CaseAction,
  DiscoveryResult,
  SearchProfile,
} from './caseflow.models';

@Injectable({ providedIn: 'root' })
export class CaseFlowApiService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api/caseflow';

  createCase(data: Partial<CaseFlowCase>): Observable<CaseFlowCase> {
    return this.http.post<CaseFlowCase>(`${this.base}/cases`, data);
  }

  listCases(
    filters: Record<string, string> = {}
  ): Observable<{ items: CaseFlowCase[]; total: number }> {
    let params = new HttpParams();
    Object.entries(filters).forEach(([k, v]) => {
      if (v) params = params.set(k, v);
    });
    return this.http.get<{ items: CaseFlowCase[]; total: number }>(
      `${this.base}/cases`,
      { params }
    );
  }

  getCase(id: string): Observable<CaseFlowCase> {
    return this.http.get<CaseFlowCase>(`${this.base}/cases/${id}`);
  }

  updateCase(id: string, data: Partial<CaseFlowCase>): Observable<CaseFlowCase> {
    return this.http.patch<CaseFlowCase>(`${this.base}/cases/${id}`, data);
  }

  transitionCase(
    id: string,
    toStatus: string,
    actor: string,
    reason?: string
  ): Observable<{ ok: boolean; error_code?: string }> {
    return this.http.post<{ ok: boolean; error_code?: string }>(
      `${this.base}/cases/${id}/transition`,
      { to_status: toStatus, actor, reason }
    );
  }

  getTimeline(caseId: string): Observable<CaseEvent[]> {
    return this.http.get<CaseEvent[]>(`${this.base}/cases/${caseId}/timeline`);
  }

  getArtifacts(caseId: string): Observable<CaseArtifact[]> {
    return this.http.get<CaseArtifact[]>(`${this.base}/cases/${caseId}/artifacts`);
  }

  addArtifact(caseId: string, data: Partial<CaseArtifact>): Observable<CaseArtifact> {
    return this.http.post<CaseArtifact>(`${this.base}/cases/${caseId}/artifacts`, data);
  }

  getActions(caseId: string): Observable<CaseAction[]> {
    return this.http.get<CaseAction[]>(`${this.base}/cases/${caseId}/actions`);
  }

  addAction(caseId: string, data: Partial<CaseAction>): Observable<CaseAction> {
    return this.http.post<CaseAction>(`${this.base}/cases/${caseId}/actions`, data);
  }

  getOpenActions(): Observable<CaseAction[]> {
    return this.http.get<CaseAction[]>(`${this.base}/actions/open`);
  }

  // Discovery
  listSearchProfiles(): Observable<SearchProfile[]> {
    return this.http.get<SearchProfile[]>(`${this.base}/discovery/profiles`);
  }

  createSearchProfile(data: Partial<SearchProfile>): Observable<SearchProfile> {
    return this.http.post<SearchProfile>(`${this.base}/discovery/profiles`, data);
  }

  runDiscovery(profileId: string): Observable<{ run_id: string }> {
    return this.http.post<{ run_id: string }>(
      `${this.base}/discovery/profiles/${profileId}/run`,
      {}
    );
  }

  getRunResults(runId: string): Observable<DiscoveryResult[]> {
    return this.http.get<DiscoveryResult[]>(
      `${this.base}/discovery/runs/${runId}/results`
    );
  }

  convertResult(
    resultId: string,
    caseType: string,
    approvedBy: string
  ): Observable<CaseFlowCase> {
    return this.http.post<CaseFlowCase>(
      `${this.base}/discovery/results/${resultId}/convert`,
      { case_type: caseType, approved_by: approvedBy }
    );
  }

  ignoreResult(resultId: string): Observable<{ ok: boolean }> {
    return this.http.post<{ ok: boolean }>(
      `${this.base}/discovery/results/${resultId}/ignore`,
      {}
    );
  }
}
