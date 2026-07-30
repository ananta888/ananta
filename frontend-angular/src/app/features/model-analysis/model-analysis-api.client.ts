import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { ApiBaseService } from '../../services/api-base.service';
import {
  ModelAnalysisCapabilities,
  ModelAnalysisGraph,
  ModelAnalysisJob,
  ModelAnalysisJobPage,
  ModelAnalysisReport,
  StartModelAnalysisRequest,
} from './model-analysis.models';

export const MODEL_ANALYSIS_MAX_GRAPH_NODES = 200;
export const MODEL_ANALYSIS_MAX_GRAPH_EDGES = 400;

@Injectable({ providedIn: 'root' })
export class ModelAnalysisApiClient extends ApiBaseService {
  private endpoint(hubUrl: string, path: string): string {
    return `${hubUrl.replace(/\/+$/, '')}/api/model-intelligence${path}`;
  }

  capabilities(hubUrl: string): Observable<ModelAnalysisCapabilities> {
    return this.core.get<ModelAnalysisCapabilities>(
      this.endpoint(hubUrl, '/capabilities'),
      hubUrl,
      undefined,
      false,
    );
  }

  listJobs(
    hubUrl: string,
    cursor = '',
    pageSize = 50,
  ): Observable<ModelAnalysisJobPage | ModelAnalysisJob[]> {
    const query = new URLSearchParams();
    query.set('page_size', String(Math.min(100, Math.max(1, pageSize))));
    if (cursor) query.set('cursor', cursor);
    return this.core.get<ModelAnalysisJobPage | ModelAnalysisJob[]>(
      this.endpoint(hubUrl, `/jobs?${query.toString()}`),
      hubUrl,
      undefined,
      false,
    );
  }

  startJob(
    hubUrl: string,
    request: StartModelAnalysisRequest,
    idempotencyKey: string,
  ): Observable<ModelAnalysisJob> {
    return this.core.request<ModelAnalysisJob>(
      'POST',
      this.endpoint(hubUrl, '/jobs'),
      hubUrl,
      {
        body: request,
        headers: { 'Idempotency-Key': idempotencyKey },
        timeoutMs: 30_000,
      },
    );
  }

  getJob(hubUrl: string, jobId: string): Observable<ModelAnalysisJob> {
    return this.core.get<ModelAnalysisJob>(
      this.endpoint(hubUrl, `/jobs/${encodeURIComponent(jobId)}`),
      hubUrl,
      undefined,
      false,
    );
  }

  cancelJob(
    hubUrl: string,
    jobId: string,
    idempotencyKey: string,
  ): Observable<ModelAnalysisJob> {
    return this.core.request<ModelAnalysisJob>(
      'POST',
      this.endpoint(hubUrl, `/jobs/${encodeURIComponent(jobId)}/cancel`),
      hubUrl,
      {
        body: { reason: 'operator_requested' },
        headers: { 'Idempotency-Key': idempotencyKey },
      },
    );
  }

  getReport(hubUrl: string, jobId: string): Observable<ModelAnalysisReport> {
    return this.core.get<ModelAnalysisReport>(
      this.endpoint(hubUrl, `/jobs/${encodeURIComponent(jobId)}/report`),
      hubUrl,
      undefined,
      false,
    );
  }

  getGraph(hubUrl: string, jobId: string): Observable<ModelAnalysisGraph> {
    const query = new URLSearchParams({
      max_nodes: String(MODEL_ANALYSIS_MAX_GRAPH_NODES),
      max_edges: String(MODEL_ANALYSIS_MAX_GRAPH_EDGES),
    });
    return this.core.get<ModelAnalysisGraph>(
      this.endpoint(
        hubUrl,
        `/jobs/${encodeURIComponent(jobId)}/graph?${query.toString()}`,
      ),
      hubUrl,
      undefined,
      false,
    );
  }
}
