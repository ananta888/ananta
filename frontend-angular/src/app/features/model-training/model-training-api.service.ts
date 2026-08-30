import { Injectable } from '@angular/core';
import { HttpEventType } from '@angular/common/http';
import { Observable, filter, map } from 'rxjs';

import { ApiBaseService } from '../../services/api-base.service';
import {
  AdapterDecisionRequest,
  AdapterExportDownload,
  AdapterExportResult,
  AdapterImportInput,
  AdapterRuntimeManagementRequest,
  AdapterRuntimeRollbackResult,
  AdapterRuntimeUnloadResult,
  AdapterSummary,
  AttachValidationDatasetRequest,
  CreateTrainingJobRequest,
  DendriticDryRunResult,
  DendriticExperimentRequest,
  DendriticRunAcceptance,
  DatasetDetail,
  DatasetDeletionResult,
  DatasetListFilters,
  DatasetRecord,
  DatasetSummary,
  DatasetUploadInput,
  DatasetUploadEvent,
  DatasetValidationReport,
  EvaluationReport,
  EvaluationScorerName,
  ResearchRecipeRequest,
  ResearchResolvedRecipe,
  ResearchRunAcceptance,
  ResearchTrainingPreflight,
  ResearchTrainingRequest,
  TrainingCapabilities,
  TrainingBackendRecommendation,
  TrainingBackendRecommendationRequest,
  TrainingEventPage,
  TrainingJobAcceptance,
  TrainingJobDetail,
  TrainingJobListFilters,
  TrainingJobSummary,
  TrainingPage,
  UnslothStorageReadModel,
} from './model-training.models';

function queryString(values: Record<string, unknown>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || value === null || value === '') continue;
    query.set(key, String(value));
  }
  const result = query.toString();
  return result ? `?${result}` : '';
}

function unwrapEnvelope(value: unknown): unknown {
  let current = value;
  for (let index = 0; index < 4; index += 1) {
    if (current && typeof current === 'object' && 'status' in current && 'data' in current) {
      current = (current as Record<string, unknown>)['data'];
      continue;
    }
    break;
  }
  return current;
}

@Injectable({ providedIn: 'root' })
export class ModelTrainingApiService extends ApiBaseService {
  private endpoint(hubUrl: string, path: string): string {
    return `${hubUrl.replace(/\/+$/, '')}/api/ml-intern-training${path}`;
  }

  private runtimeEndpoint(hubUrl: string, path: string): string {
    return `${hubUrl.replace(/\/+$/, '')}/api/ml-intern-lora-runtime${path}`;
  }

  private dendriticEndpoint(hubUrl: string, path: string): string {
    return `${hubUrl.replace(/\/+$/, '')}/api/ml-intern-training/dendritic-memory${path}`;
  }

  private researchEndpoint(hubUrl: string, path: string): string {
    return `${hubUrl.replace(/\/+$/, '')}/api/ml-intern-training/research${path}`;
  }

  capabilities(hubUrl: string): Observable<TrainingCapabilities> {
    return this.core.get<TrainingCapabilities>(this.endpoint(hubUrl, '/capabilities'), hubUrl, undefined, false);
  }

  dryRunDendriticExperiment(
    hubUrl: string,
    payload: DendriticExperimentRequest,
  ): Observable<DendriticDryRunResult> {
    return this.core.post<DendriticDryRunResult>(this.dendriticEndpoint(hubUrl, '/dry-run'), payload, hubUrl);
  }

  createDendriticExperiment(
    hubUrl: string,
    payload: DendriticExperimentRequest,
    key: string,
  ): Observable<DendriticRunAcceptance> {
    return this.core.request<DendriticRunAcceptance>('POST', this.dendriticEndpoint(hubUrl, '/runs'), hubUrl, {
      body: payload,
      headers: { 'Idempotency-Key': key },
      timeoutMs: 30_000,
    });
  }

  resolveResearchRecipe(hubUrl: string, payload: ResearchRecipeRequest): Observable<ResearchResolvedRecipe> {
    return this.core.post<ResearchResolvedRecipe>(this.researchEndpoint(hubUrl, '/recipes/resolve'), payload, hubUrl);
  }

  dryRunResearchTraining(
    hubUrl: string,
    payload: ResearchTrainingRequest,
  ): Observable<ResearchTrainingPreflight> {
    return this.core.post<ResearchTrainingPreflight>(this.researchEndpoint(hubUrl, '/dry-run'), payload, hubUrl);
  }

  createResearchTraining(
    hubUrl: string,
    payload: ResearchTrainingRequest,
    key: string,
  ): Observable<ResearchRunAcceptance> {
    return this.core.request<ResearchRunAcceptance>('POST', this.researchEndpoint(hubUrl, '/runs'), hubUrl, {
      body: payload,
      headers: { 'Idempotency-Key': key },
      timeoutMs: 30_000,
    });
  }

  recommendBackend(
    hubUrl: string,
    input: TrainingBackendRecommendationRequest,
  ): Observable<TrainingBackendRecommendation> {
    return this.core.post<TrainingBackendRecommendation>(
      this.endpoint(hubUrl, '/backends/recommendation'), input, hubUrl,
    );
  }

  unslothStorage(hubUrl: string): Observable<UnslothStorageReadModel> {
    return this.core.get<UnslothStorageReadModel>(
      this.endpoint(hubUrl, '/unsloth/storage'), hubUrl, undefined, false,
    );
  }

  listDatasets(hubUrl: string, filters: DatasetListFilters = {}): Observable<TrainingPage<DatasetSummary> | DatasetSummary[]> {
    return this.core.get<TrainingPage<DatasetSummary> | DatasetSummary[]>(
      this.endpoint(hubUrl, `/datasets${queryString({ ...filters, limit: filters.limit || 50 })}`), hubUrl, undefined, false,
    );
  }

  getDataset(hubUrl: string, datasetId: string): Observable<DatasetDetail> {
    return this.core.get<DatasetDetail>(this.endpoint(hubUrl, `/datasets/${encodeURIComponent(datasetId)}`), hubUrl, undefined, false);
  }

  uploadDataset(hubUrl: string, input: DatasetUploadInput, key: string): Observable<DatasetDetail> {
    const form = this.datasetUploadForm(input);
    return this.core.request<DatasetDetail>('POST', this.endpoint(hubUrl, '/datasets'), hubUrl, {
      body: form,
      headers: { 'Idempotency-Key': key },
      timeoutMs: 120_000,
    });
  }

  uploadDatasetWithProgress(hubUrl: string, input: DatasetUploadInput, key: string): Observable<DatasetUploadEvent> {
    const form = this.datasetUploadForm(input);
    return this.core.requestEvents<unknown>('POST', this.endpoint(hubUrl, '/datasets'), hubUrl, {
      body: form,
      headers: { 'Idempotency-Key': key },
      timeoutMs: 120_000,
    }).pipe(
      map((event): DatasetUploadEvent | null => {
        if (event.type === HttpEventType.UploadProgress) {
          const total = Number(event.total || 0);
          const loaded = Math.max(0, Number(event.loaded || 0));
          return {
            kind: 'progress' as const,
            loaded,
            total: total > 0 ? total : undefined,
            percent: total > 0 ? Math.min(100, Math.round((loaded / total) * 100)) : undefined,
          };
        }
        if (event.type === HttpEventType.Response) {
          return { kind: 'complete' as const, dataset: unwrapEnvelope(event.body) as DatasetDetail };
        }
        return null;
      }),
      filter((event): event is DatasetUploadEvent => event !== null),
    );
  }

  private datasetUploadForm(input: DatasetUploadInput): FormData {
    const form = new FormData();
    form.append('file', input.file, input.file.name);
    if (input.name?.trim()) form.append('name', input.name.trim());
    form.append('purpose', input.purpose.trim());
    form.append('license', input.license.trim());
    form.append('privacy', input.privacy);
    form.append('validation_ratio', String(input.validation_ratio));
    form.append('split_seed', String(input.split_seed));
    return form;
  }

  listDatasetRecords(
    hubUrl: string,
    datasetId: string,
    split: 'train' | 'validation',
    cursor = '',
    limit = 25,
  ): Observable<TrainingPage<DatasetRecord>> {
    const query = queryString({ split, cursor, limit: Math.min(100, Math.max(1, limit)) });
    return this.core.get<TrainingPage<DatasetRecord>>(
      this.endpoint(hubUrl, `/datasets/${encodeURIComponent(datasetId)}/records${query}`), hubUrl, undefined, false,
    );
  }

  splitDataset(
    hubUrl: string,
    datasetId: string,
    payload: { validation_ratio: number; seed: number; overwrite: boolean },
    key: string,
  ): Observable<DatasetDetail> {
    return this.core.request<DatasetDetail>('POST', this.endpoint(hubUrl, `/datasets/${encodeURIComponent(datasetId)}/split`), hubUrl, {
      body: payload,
      headers: { 'Idempotency-Key': key },
    });
  }

  attachValidationDataset(
    hubUrl: string,
    datasetId: string,
    payload: AttachValidationDatasetRequest,
    key: string,
  ): Observable<DatasetDetail> {
    return this.core.request<DatasetDetail>(
      'POST',
      this.endpoint(hubUrl, `/datasets/${encodeURIComponent(datasetId)}/validation-dataset`),
      hubUrl,
      { body: payload, headers: { 'Idempotency-Key': key }, timeoutMs: 120_000 },
    );
  }

  deleteDataset(hubUrl: string, datasetId: string, key: string): Observable<DatasetDeletionResult> {
    return this.core.request<DatasetDeletionResult>(
      'DELETE',
      this.endpoint(hubUrl, `/datasets/${encodeURIComponent(datasetId)}`),
      hubUrl,
      { headers: { 'Idempotency-Key': key } },
    );
  }

  validateDataset(hubUrl: string, datasetId: string, key: string): Observable<DatasetValidationReport> {
    return this.core.request<DatasetValidationReport>('POST', this.endpoint(hubUrl, `/datasets/${encodeURIComponent(datasetId)}/validate`), hubUrl, {
      body: {},
      headers: { 'Idempotency-Key': key },
      timeoutMs: 120_000,
    });
  }

  listJobs(hubUrl: string, filters: TrainingJobListFilters = {}): Observable<TrainingPage<TrainingJobSummary> | TrainingJobSummary[]> {
    return this.core.get<TrainingPage<TrainingJobSummary> | TrainingJobSummary[]>(
      this.endpoint(hubUrl, `/jobs${queryString({ ...filters, limit: filters.limit || 50 })}`), hubUrl, undefined, false,
    );
  }

  createJob(hubUrl: string, payload: CreateTrainingJobRequest, key: string): Observable<TrainingJobAcceptance> {
    return this.core.request<TrainingJobAcceptance>('POST', this.endpoint(hubUrl, '/jobs'), hubUrl, {
      body: payload,
      headers: { 'Idempotency-Key': key },
      timeoutMs: 30_000,
    });
  }

  getJob(hubUrl: string, jobId: string): Observable<TrainingJobDetail> {
    return this.core.get<TrainingJobDetail>(this.endpoint(hubUrl, `/jobs/${encodeURIComponent(jobId)}`), hubUrl, undefined, false);
  }

  listJobEvents(hubUrl: string, jobId: string, afterSequence = 0, limit = 200): Observable<TrainingEventPage> {
    const query = queryString({ after_sequence: Math.max(0, afterSequence), limit: Math.min(500, Math.max(1, limit)) });
    return this.core.get<TrainingEventPage>(
      this.endpoint(hubUrl, `/jobs/${encodeURIComponent(jobId)}/events${query}`), hubUrl, undefined, false,
    );
  }

  streamJobEvents(hubUrl: string, jobId: string, afterSequence = 0): Observable<unknown> {
    return new Observable(observer => {
      const token = this.core.currentUserToken();
      if (!token) {
        observer.error(new Error('training_event_stream_auth_required'));
        return undefined;
      }
      const query = queryString({ after_sequence: Math.max(0, afterSequence), limit: 200, stream: true });
      const abort = new AbortController();
      let closed = false;
      const emitFrame = (frame: string): void => {
        const data = frame
          .split('\n')
          .filter(line => line.startsWith('data:'))
          .map(line => line.slice(5).trimStart())
          .join('\n');
        if (!data) return;
        try {
          observer.next(JSON.parse(data));
        } catch {
          // Heartbeats or non-JSON frames do not become domain events.
        }
      };
      void (async () => {
        try {
          const response = await fetch(
            this.endpoint(hubUrl, `/jobs/${encodeURIComponent(jobId)}/events${query}`),
            {
              method: 'GET',
              credentials: 'same-origin',
              cache: 'no-store',
              signal: abort.signal,
              headers: { Accept: 'text/event-stream', Authorization: `Bearer ${token}` },
            },
          );
          if (!response.ok || !response.body) throw new Error(`training_event_stream_http_${response.status}`);
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          while (!closed) {
            const chunk = await reader.read();
            buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !chunk.done }).replace(/\r\n/g, '\n');
            if (buffer.length > 1_048_576) throw new Error('training_event_stream_frame_too_large');
            let boundary = buffer.indexOf('\n\n');
            while (boundary >= 0) {
              emitFrame(buffer.slice(0, boundary));
              buffer = buffer.slice(boundary + 2);
              boundary = buffer.indexOf('\n\n');
            }
            if (chunk.done) break;
          }
          if (!closed) observer.complete();
        } catch (error) {
          if (!closed && !abort.signal.aborted) observer.error(error);
        }
      })();
      return () => {
        closed = true;
        abort.abort();
      };
    });
  }

  cancelJob(hubUrl: string, jobId: string, reason: string, key: string): Observable<TrainingJobDetail> {
    return this.core.request<TrainingJobDetail>('POST', this.endpoint(hubUrl, `/jobs/${encodeURIComponent(jobId)}/cancel`), hubUrl, {
      body: { reason: reason.trim() },
      headers: { 'Idempotency-Key': key },
    });
  }

  listAdapters(hubUrl: string): Observable<TrainingPage<AdapterSummary> | AdapterSummary[]> {
    return this.core.get<TrainingPage<AdapterSummary> | AdapterSummary[]>(this.endpoint(hubUrl, '/adapters?limit=100'), hubUrl, undefined, false);
  }

  importAdapter(hubUrl: string, input: AdapterImportInput, key: string): Observable<AdapterSummary> {
    const form = new FormData();
    form.append('name', input.name.trim());
    form.append('base_model_id', input.base_model_id.trim());
    form.append('method', input.method);
    if (input.bundle) form.append('bundle', input.bundle, input.bundle.name);
    if (input.config) form.append('adapter_config', input.config, input.config.name);
    if (input.weights) form.append('adapter_model', input.weights, input.weights.name);
    return this.core.request<AdapterSummary>('POST', this.endpoint(hubUrl, '/adapters/import'), hubUrl, {
      body: form,
      headers: { 'Idempotency-Key': key },
      timeoutMs: 120_000,
    });
  }

  evaluateAdapter(
    hubUrl: string,
    adapterId: string,
    datasetId: string,
    scorerName: EvaluationScorerName,
    liveConfirmed: boolean,
    riskReason: string,
    key: string,
  ): Observable<EvaluationReport> {
    return this.core.request<EvaluationReport>('POST', this.endpoint(hubUrl, '/evaluations'), hubUrl, {
      body: {
        adapter_id: adapterId,
        dataset_id: datasetId,
        scorer_name: scorerName,
        ...(liveConfirmed ? { live_confirmed: true, risk_reason: riskReason.trim() } : {}),
      },
      headers: { 'Idempotency-Key': key },
      timeoutMs: 30_000,
    });
  }

  getEvaluation(hubUrl: string, evaluationId: string): Observable<EvaluationReport> {
    return this.core.get<EvaluationReport>(this.endpoint(hubUrl, `/evaluations/${encodeURIComponent(evaluationId)}`), hubUrl, undefined, false);
  }

  decideAdapter(
    hubUrl: string,
    adapterId: string,
    action: 'approve' | 'reject' | 'deprecate' | 'rollback',
    payload: AdapterDecisionRequest,
    key: string,
  ): Observable<AdapterSummary> {
    return this.core.request<AdapterSummary>('POST', this.endpoint(hubUrl, `/adapters/${encodeURIComponent(adapterId)}/${action}`), hubUrl, {
      body: payload,
      headers: { 'Idempotency-Key': key },
    });
  }

  exportAdapter(hubUrl: string, adapterId: string, key: string): Observable<AdapterExportResult> {
    return this.core.request<AdapterExportResult>(
      'POST', this.endpoint(hubUrl, `/adapters/${encodeURIComponent(adapterId)}/export`), hubUrl,
      { body: {}, headers: { 'Idempotency-Key': key }, timeoutMs: 120_000 },
    );
  }

  downloadAdapterExport(hubUrl: string, artifactId: string): Observable<AdapterExportDownload> {
    const filenameStem = artifactId.replace(/[^A-Za-z0-9._-]/g, '_').slice(0, 160) || 'lora-adapter-export';
    return this.core.requestBlob(
      this.endpoint(hubUrl, `/exports/${encodeURIComponent(artifactId)}`),
      hubUrl,
      120_000,
    ).pipe(map(response => ({
      blob: response.body || new Blob([], { type: 'application/zip' }),
      filename: `${filenameStem}.zip`,
      sha256: response.headers.get('X-Artifact-SHA256') || undefined,
    })));
  }

  unloadRuntimeAdapter(
    hubUrl: string,
    adapterId: string,
    payload: AdapterRuntimeManagementRequest,
  ): Observable<AdapterRuntimeUnloadResult> {
    return this.core.request<AdapterRuntimeUnloadResult>(
      'POST',
      this.runtimeEndpoint(hubUrl, `/adapters/${encodeURIComponent(adapterId)}/unload`),
      hubUrl,
      { body: payload },
    );
  }

  rollbackRuntimeAdapter(
    hubUrl: string,
    adapterId: string,
    payload: AdapterRuntimeManagementRequest,
  ): Observable<AdapterRuntimeRollbackResult> {
    return this.core.request<AdapterRuntimeRollbackResult>(
      'POST',
      this.runtimeEndpoint(hubUrl, `/adapters/${encodeURIComponent(adapterId)}/rollback`),
      hubUrl,
      { body: payload },
    );
  }
}
