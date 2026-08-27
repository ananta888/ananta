import { Injectable, OnDestroy, inject, signal } from '@angular/core';
import { Observable, finalize, map, tap } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { ModelTrainingApiService } from './model-training-api.service';
import { ModelTrainingJobMonitorService } from './model-training-job-monitor.service';
import { apiErrorMessage, boundedText } from './model-training-status';
import {
  entityFrom,
  normalizeAdapter,
  normalizeDataset,
  normalizeDatasetRecord,
  normalizeDatasetSummary,
  normalizeEvaluation,
  normalizePage,
  normalizeTrainingJob,
  normalizeTrainingJobAcceptance,
  normalizeUnslothStorage,
  normalizeValidationReport,
} from './model-training-normalizers';
import {
  AdapterDecisionRequest,
  AdapterImportInput,
  AdapterRuntimeManagementRequest,
  AdapterRuntimeRollbackResult,
  AdapterRuntimeUnloadResult,
  AdapterSummary,
  CreateTrainingJobRequest,
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
  TrainingCapabilities,
  TrainingBackendRecommendation,
  TrainingBackendRecommendationRequest,
  TrainingJobAcceptance,
  TrainingJobDetail,
  TrainingJobListFilters,
  TrainingJobSummary,
  UnslothStorageReadModel,
} from './model-training.models';

@Injectable()
export class ModelTrainingFacade implements OnDestroy {
  private readonly directory = inject(AgentDirectoryService);
  private readonly api = inject(ModelTrainingApiService);
  readonly monitor = inject(ModelTrainingJobMonitorService);

  readonly hubUrl = signal('');
  readonly capabilities = signal<TrainingCapabilities | null>(null);
  readonly datasets = signal<DatasetSummary[]>([]);
  readonly datasetCount = signal(0);
  readonly datasetNextCursor = signal<string | null>(null);
  readonly selectedDataset = signal<DatasetDetail | null>(null);
  readonly records = signal<DatasetRecord[]>([]);
  readonly recordsCount = signal(0);
  readonly recordsNextCursor = signal<string | null>(null);
  readonly recordSplit = signal<'train' | 'validation'>('train');
  readonly jobs = signal<TrainingJobSummary[]>([]);
  readonly jobCount = signal(0);
  readonly adapters = signal<AdapterSummary[]>([]);
  readonly selectedAdapter = signal<AdapterSummary | null>(null);
  readonly selectedEvaluation = signal<EvaluationReport | null>(null);
  readonly unslothStorage = signal<UnslothStorageReadModel | null>(null);

  readonly loadingCapabilities = signal(false);
  readonly loadingDatasets = signal(false);
  readonly loadingDatasetDetail = signal(false);
  readonly loadingRecords = signal(false);
  readonly loadingJobs = signal(false);
  readonly loadingAdapters = signal(false);
  readonly loadingUnslothStorage = signal(false);
  readonly error = signal('');

  private recordCursors: string[] = [''];
  private recordCursorIndex = 0;

  constructor() {
    this.resolveHub();
  }

  resolveHub(): string {
    const url = String(this.directory.list().find(agent => agent.role === 'hub')?.url || '').replace(/\/+$/, '');
    this.hubUrl.set(url);
    return url;
  }

  loadOverview(): void {
    this.loadCapabilities();
    this.loadDatasets();
    this.loadJobs();
    this.loadAdapters();
    this.loadUnslothStorage();
  }

  loadCapabilities(): void {
    const hubUrl = this.resolveHub();
    if (!hubUrl) return;
    this.loadingCapabilities.set(true);
    this.api.capabilities(hubUrl).pipe(finalize(() => this.loadingCapabilities.set(false))).subscribe({
      next: value => this.capabilities.set(entityFrom(value, 'capabilities') as TrainingCapabilities),
      error: error => this.captureError(error, 'Training-Capabilities konnten nicht geladen werden'),
    });
  }

  loadUnslothStorage(): void {
    const hubUrl = this.hubUrl() || this.resolveHub();
    if (!hubUrl) return;
    this.loadingUnslothStorage.set(true);
    this.api.unslothStorage(hubUrl)
      .pipe(finalize(() => this.loadingUnslothStorage.set(false)))
      .subscribe({
        next: value => this.unslothStorage.set(normalizeUnslothStorage(value)),
        error: error => this.captureError(error, 'Unsloth-Storage-Status konnte nicht geladen werden'),
      });
  }

  loadDatasets(filters: DatasetListFilters = {}): void {
    const hubUrl = this.hubUrl() || this.resolveHub();
    if (!hubUrl) return;
    this.loadingDatasets.set(true);
    this.api.listDatasets(hubUrl, filters).pipe(finalize(() => this.loadingDatasets.set(false))).subscribe({
      next: value => {
        const page = normalizePage(value, ['datasets'], normalizeDatasetSummary);
        this.datasets.set(page.items);
        this.datasetCount.set(page.count);
        this.datasetNextCursor.set(page.next_cursor || null);
      },
      error: error => this.captureError(error, 'Datasets konnten nicht geladen werden'),
    });
  }

  uploadDataset(input: DatasetUploadInput, key: string): Observable<DatasetDetail> {
    return this.api.uploadDataset(this.hubUrl(), input, key).pipe(
      map(normalizeDataset),
      tap(dataset => {
        this.selectedDataset.set(dataset);
        this.loadDatasets();
        this.loadRecords(dataset.id, 'train');
      }),
    );
  }

  uploadDatasetWithProgress(input: DatasetUploadInput, key: string): Observable<DatasetUploadEvent> {
    return this.api.uploadDatasetWithProgress(this.hubUrl(), input, key).pipe(
      map(event => event.kind === 'complete'
        ? { ...event, dataset: normalizeDataset(event.dataset) }
        : event),
      tap(event => {
        if (event.kind !== 'complete') return;
        this.selectedDataset.set(event.dataset);
        this.loadDatasets();
        this.loadRecords(event.dataset.id, 'train');
      }),
    );
  }

  selectDataset(datasetId: string): void {
    if (!datasetId || !this.hubUrl()) return;
    this.loadingDatasetDetail.set(true);
    this.api.getDataset(this.hubUrl(), datasetId).pipe(finalize(() => this.loadingDatasetDetail.set(false))).subscribe({
      next: value => {
        const dataset = normalizeDataset(value);
        this.selectedDataset.set(dataset);
        this.loadRecords(dataset.id, this.recordSplit());
      },
      error: error => this.captureError(error, 'Dataset-Details konnten nicht geladen werden'),
    });
  }

  loadRecords(datasetId: string, split: 'train' | 'validation', cursor = '', remember = false): void {
    if (!datasetId || !this.hubUrl()) return;
    if (!remember) {
      this.recordCursors = [''];
      this.recordCursorIndex = 0;
      cursor = '';
    }
    this.recordSplit.set(split);
    this.loadingRecords.set(true);
    this.api.listDatasetRecords(this.hubUrl(), datasetId, split, cursor).pipe(finalize(() => this.loadingRecords.set(false))).subscribe({
      next: value => {
        const page = normalizePage(value, ['records'], item => normalizeDatasetRecord(item, split));
        this.records.set(page.items);
        this.recordsCount.set(page.count);
        this.recordsNextCursor.set(page.next_cursor || null);
      },
      error: error => this.captureError(error, 'Dataset-Preview konnte nicht geladen werden'),
    });
  }

  nextRecordPage(): void {
    const dataset = this.selectedDataset();
    const cursor = this.recordsNextCursor();
    if (!dataset || !cursor) return;
    this.recordCursors = this.recordCursors.slice(0, this.recordCursorIndex + 1);
    this.recordCursors.push(cursor);
    this.recordCursorIndex += 1;
    this.loadRecords(dataset.id, this.recordSplit(), cursor, true);
  }

  previousRecordPage(): void {
    const dataset = this.selectedDataset();
    if (!dataset || this.recordCursorIndex <= 0) return;
    this.recordCursorIndex -= 1;
    this.loadRecords(dataset.id, this.recordSplit(), this.recordCursors[this.recordCursorIndex], true);
  }

  hasPreviousRecordPage(): boolean { return this.recordCursorIndex > 0; }

  splitDataset(datasetId: string, validationRatio: number, seed: number, overwrite: boolean, key: string): Observable<DatasetDetail> {
    return this.api.splitDataset(this.hubUrl(), datasetId, {
      validation_ratio: validationRatio, seed, overwrite,
    }, key).pipe(
      map(normalizeDataset),
      tap(dataset => {
        this.selectedDataset.set(dataset);
        this.loadDatasets();
        this.loadRecords(dataset.id, 'train');
      }),
    );
  }

  attachValidationDataset(datasetId: string, validationDatasetId: string, key: string): Observable<DatasetDetail> {
    return this.api.attachValidationDataset(this.hubUrl(), datasetId, {
      validation_dataset_id: validationDatasetId,
    }, key).pipe(
      map(normalizeDataset),
      tap(dataset => {
        this.selectedDataset.set(dataset);
        this.loadDatasets();
        this.loadRecords(dataset.id, 'validation');
      }),
    );
  }

  deleteDataset(datasetId: string, key: string): Observable<DatasetDeletionResult> {
    return this.api.deleteDataset(this.hubUrl(), datasetId, key).pipe(
      tap(() => {
        if (this.selectedDataset()?.id === datasetId) {
          this.selectedDataset.set(null);
          this.records.set([]);
          this.recordsCount.set(0);
          this.recordsNextCursor.set(null);
        }
        this.loadDatasets();
      }),
    );
  }

  validateDataset(datasetId: string, key: string): Observable<DatasetValidationReport> {
    return this.api.validateDataset(this.hubUrl(), datasetId, key).pipe(
      map(value => normalizeValidationReport(value, datasetId)),
      tap(report => {
        this.selectedDataset.update(dataset => dataset ? { ...dataset, validation_report: report, validation_status: report.valid ? 'valid' : 'invalid', trainable: report.trainable ?? report.valid } : dataset);
        this.loadDatasets();
      }),
    );
  }

  loadJobs(filters: TrainingJobListFilters = {}): void {
    const hubUrl = this.hubUrl() || this.resolveHub();
    if (!hubUrl) return;
    this.loadingJobs.set(true);
    this.api.listJobs(hubUrl, filters).pipe(finalize(() => this.loadingJobs.set(false))).subscribe({
      next: value => {
        const page = normalizePage(value, ['jobs'], normalizeTrainingJob);
        this.jobs.set(page.items);
        this.jobCount.set(page.count);
      },
      error: error => this.captureError(error, 'Trainingsjobs konnten nicht geladen werden'),
    });
  }

  createJob(payload: CreateTrainingJobRequest, key: string): Observable<TrainingJobAcceptance> {
    return this.api.createJob(this.hubUrl(), payload, key).pipe(
      map(normalizeTrainingJobAcceptance),
      tap(accepted => {
        if (accepted.job_id) this.monitor.start(this.hubUrl(), accepted.job_id);
        this.loadJobs();
      }),
    );
  }

  recommendBackend(payload: TrainingBackendRecommendationRequest): Observable<TrainingBackendRecommendation> {
    return this.api.recommendBackend(this.hubUrl(), payload).pipe(
      map(value => entityFrom(value, 'recommendation') as TrainingBackendRecommendation),
    );
  }

  selectJob(jobId: string): void {
    if (jobId && this.hubUrl()) this.monitor.start(this.hubUrl(), jobId);
  }

  cancelJob(jobId: string, reason: string, key: string): Observable<TrainingJobDetail> {
    return this.api.cancelJob(this.hubUrl(), jobId, reason, key).pipe(
      map(normalizeTrainingJob),
      tap(() => {
        this.monitor.refresh();
        this.loadJobs();
      }),
    );
  }

  loadAdapters(): void {
    const hubUrl = this.hubUrl() || this.resolveHub();
    if (!hubUrl) return;
    this.loadingAdapters.set(true);
    this.api.listAdapters(hubUrl).pipe(finalize(() => this.loadingAdapters.set(false))).subscribe({
      next: value => {
        const page = normalizePage(value, ['adapters'], normalizeAdapter);
        this.adapters.set(page.items);
        const selectedId = this.selectedAdapter()?.id;
        if (selectedId) this.selectedAdapter.set(page.items.find(item => item.id === selectedId) || null);
      },
      error: error => this.captureError(error, 'Adapter konnten nicht geladen werden'),
    });
  }

  selectAdapter(adapter: AdapterSummary | null): void {
    this.selectedAdapter.set(adapter);
    this.selectedEvaluation.set(null);
    if (adapter?.evaluation_id) this.loadEvaluation(adapter.evaluation_id);
  }

  importAdapter(input: AdapterImportInput, key: string): Observable<AdapterSummary> {
    return this.api.importAdapter(this.hubUrl(), input, key).pipe(
      map(normalizeAdapter),
      tap(adapter => {
        this.selectedAdapter.set(adapter);
        this.loadAdapters();
      }),
    );
  }

  evaluateAdapter(
    adapterId: string,
    datasetId: string,
    scorerName: EvaluationScorerName,
    liveConfirmed: boolean,
    riskReason: string,
    key: string,
  ): Observable<EvaluationReport> {
    return this.api.evaluateAdapter(
      this.hubUrl(), adapterId, datasetId, scorerName, liveConfirmed, riskReason, key,
    ).pipe(
      map(normalizeEvaluation),
      tap(report => this.selectedEvaluation.set(report)),
    );
  }

  loadEvaluation(evaluationId: string): void {
    this.api.getEvaluation(this.hubUrl(), evaluationId).subscribe({
      next: value => this.selectedEvaluation.set(normalizeEvaluation(value)),
      error: error => this.captureError(error, 'Evaluation konnte nicht geladen werden'),
    });
  }

  decideAdapter(
    adapterId: string,
    action: 'approve' | 'reject' | 'deprecate' | 'rollback',
    payload: AdapterDecisionRequest,
    key: string,
  ): Observable<AdapterSummary> {
    return this.api.decideAdapter(this.hubUrl(), adapterId, action, payload, key).pipe(
      map(normalizeAdapter),
      tap(adapter => {
        this.selectedAdapter.set(adapter);
        this.loadAdapters();
      }),
    );
  }

  exportAdapter(adapterId: string, key: string) {
    return this.api.exportAdapter(this.hubUrl(), adapterId, key);
  }

  downloadAdapterExport(artifactId: string) {
    return this.api.downloadAdapterExport(this.hubUrl(), artifactId);
  }

  unloadRuntimeAdapter(adapterId: string, payload: AdapterRuntimeManagementRequest): Observable<AdapterRuntimeUnloadResult> {
    return this.api.unloadRuntimeAdapter(this.hubUrl(), adapterId, payload);
  }

  rollbackRuntimeAdapter(adapterId: string, payload: AdapterRuntimeManagementRequest): Observable<AdapterRuntimeRollbackResult> {
    return this.api.rollbackRuntimeAdapter(this.hubUrl(), adapterId, payload).pipe(
      tap(() => this.loadAdapters()),
    );
  }

  clearError(): void { this.error.set(''); }

  ngOnDestroy(): void {
    this.monitor.stop();
  }

  private captureError(error: any, fallback: string): void {
    this.error.set(boundedText(apiErrorMessage(error, fallback), 700));
  }
}
