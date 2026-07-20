import { ChangeDetectionStrategy, ChangeDetectorRef, Component, Input, OnChanges, OnDestroy, OnInit, SimpleChanges, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Observable, Subscription } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import {
  SpeechReconciliationAction,
  SpeechReconciliationApiService,
  SpeechReconciliationJobView,
  SpeechResourceVectorView,
} from '../../services/speech-reconciliation-api.service';

const TERMINAL_STATES = new Set(['completed', 'dataset_only_completed', 'failed', 'cancelled', 'expired']);

type ResourceField = keyof SpeechResourceVectorView;

interface ResourceRow {
  readonly field: ResourceField;
  readonly label: string;
  readonly unit: string;
}

@Component({
  selector: 'app-speech-reconciliation-panel',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './speech-reconciliation-panel.component.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: [`
    :host { display: block; margin-top: 1rem; }
    section { border: 1px solid currentColor; border-radius: .6rem; padding: .8rem; }
    header, .toolbar, .actions, .factor-control { display: flex; align-items: center; flex-wrap: wrap; gap: .6rem; }
    header h3 { flex: 1; margin: 0; }
    .summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); gap: .6rem; }
    .summary div, .conflicts li { border-inline-start: .3rem solid #687078; padding: .45rem; }
    dt { font-size: .8rem; opacity: .78; } dd { margin: .15rem 0 0; font-weight: 600; overflow-wrap: anywhere; }
    table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
    th, td { border-bottom: 1px solid color-mix(in srgb, currentColor 25%, transparent); padding: .4rem; text-align: end; }
    th:first-child, td:first-child { text-align: start; }
    button, input, select { font: inherit; min-height: 2.5rem; }
    button:focus-visible, input:focus-visible, select:focus-visible { outline: 3px solid currentColor; outline-offset: 2px; }
    .conflicts { display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap: .5rem; list-style: none; padding: 0; }
    .error { color: #ffb4a8; font-weight: 600; }
    .notice { opacity: .8; }
    @media (max-width: 40rem) {
      .budget { overflow-x: auto; }
      .summary { grid-template-columns: 1fr 1fr; }
    }
  `],
})
export class SpeechReconciliationPanelComponent implements OnInit, OnChanges, OnDestroy {
  @Input() hubAuthorized = false;
  @Input() hubUrl = '';
  @Input() jobId: string | null = null;
  @Input() pollIntervalMs = 5_000;

  jobs: readonly SpeechReconciliationJobView[] = [];
  selected: SpeechReconciliationJobView | null = null;
  reduceFactor = 1;
  loading = false;
  pendingAction: SpeechReconciliationAction | null = null;
  errorCode: 'hub_missing' | 'load_failed' | 'conflict' | 'forbidden' | 'request_failed' | null = null;

  readonly resourceRows: readonly ResourceRow[] = Object.freeze([
    { field: 'wall_time_ms', label: 'Wall time', unit: 'ms' },
    { field: 'cpu_time_ms', label: 'CPU time', unit: 'ms' },
    { field: 'gpu_time_ms', label: 'GPU time', unit: 'ms' },
    { field: 'memory_byte_ms', label: 'Memory', unit: 'Byte·ms' },
    { field: 'disk_bytes', label: 'Disk', unit: 'Bytes' },
    { field: 'checkpoint_bytes', label: 'Checkpoints', unit: 'Bytes' },
    { field: 'energy_millijoules', label: 'Energy', unit: 'mJ' },
  ]);

  private readonly api = inject(SpeechReconciliationApiService);
  private readonly directory = inject(AgentDirectoryService);
  private readonly changeDetector = inject(ChangeDetectorRef);
  private readSubscription: Subscription | null = null;
  private mutationSubscription: Subscription | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private readGeneration = 0;
  private mutationGeneration = 0;
  private idempotencySerial = 0;
  private initialized = false;

  ngOnInit(): void {
    this.initialized = true;
    this.refresh();
    this.armPolling();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (!this.initialized) return;
    if (changes['pollIntervalMs']) this.armPolling();
    if (changes['hubAuthorized'] || changes['hubUrl'] || changes['jobId']) this.refresh();
  }

  ngOnDestroy(): void {
    this.readGeneration += 1;
    this.mutationGeneration += 1;
    this.readSubscription?.unsubscribe();
    this.mutationSubscription?.unsubscribe();
    if (this.pollTimer !== null) clearInterval(this.pollTimer);
  }

  refresh(): void {
    const generation = ++this.readGeneration;
    this.readSubscription?.unsubscribe();
    if (!this.hubAuthorized) {
      this.resetProjection();
      return;
    }
    if (this.pendingAction) return;
    const hubUrl = this.resolveHubUrl();
    if (!hubUrl) {
      this.loading = false;
      this.errorCode = 'hub_missing';
      this.changeDetector.markForCheck();
      return;
    }
    this.loading = true;
    this.errorCode = null;
    const requestedJobId = this.jobId?.trim() || null;
    let request: Observable<SpeechReconciliationJobView | { readonly jobs: readonly SpeechReconciliationJobView[] }>;
    try {
      request = requestedJobId ? this.api.get(hubUrl, requestedJobId) : this.api.list(hubUrl, 0, 50);
    } catch {
      this.loading = false;
      this.errorCode = 'load_failed';
      this.changeDetector.markForCheck();
      return;
    }
    this.readSubscription = request.subscribe({
      next: value => {
        if (generation !== this.readGeneration || !this.hubAuthorized) return;
        if ('jobs' in value) {
          this.jobs = value.jobs;
          const selectedId = this.selected?.job_id;
          this.selectProjection(value.jobs.find(job => job.job_id === selectedId) ?? value.jobs[0] ?? null);
        } else {
          this.jobs = Object.freeze([value]);
          this.selectProjection(value);
        }
        this.loading = false;
        this.errorCode = null;
        this.changeDetector.markForCheck();
      },
      error: () => {
        if (generation !== this.readGeneration) return;
        this.loading = false;
        this.errorCode = 'load_failed';
        this.changeDetector.markForCheck();
      },
    });
  }

  selectJob(jobId: string): void {
    if (this.pendingAction) return;
    this.selectProjection(this.jobs.find(job => job.job_id === jobId) ?? null);
    this.changeDetector.markForCheck();
  }

  run(action: Exclude<SpeechReconciliationAction, 'reduce'>): void {
    this.mutate(action);
  }

  reduce(): void {
    if (!this.canReduce()) return;
    this.mutate('reduce', this.reduceFactor);
  }

  canPause(): boolean {
    return this.canMutate() && (this.selected?.state === 'queued' || this.selected?.state === 'running');
  }

  canResume(): boolean {
    return this.canMutate() && this.selected?.state === 'paused';
  }

  canCancel(): boolean {
    return this.canMutate() && !!this.selected && !TERMINAL_STATES.has(this.selected.state)
      && this.selected.state !== 'cancel_requested';
  }

  canReduce(): boolean {
    return this.canMutate()
      && !!this.selected
      && ['queued', 'running', 'paused'].includes(this.selected.state)
      && Number.isInteger(this.reduceFactor)
      && this.reduceFactor >= 1
      && this.reduceFactor < this.selected.max_compute_factor;
  }

  factorInputEnabled(): boolean {
    return this.canMutate() && !!this.selected && ['queued', 'running', 'paused'].includes(this.selected.state);
  }

  formatDuration(durationMs: number): string {
    const totalSeconds = Math.floor(durationMs / 1_000);
    const hours = Math.floor(totalSeconds / 3_600);
    const minutes = Math.floor((totalSeconds % 3_600) / 60);
    const seconds = totalSeconds % 60;
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  }

  budgetValue(vector: SpeechResourceVectorView, field: ResourceField): string {
    return vector[field].toLocaleString('de-DE');
  }

  statusMessage(): string {
    if (this.pendingAction) return `${this.actionLabel(this.pendingAction)} wird vom Hub bestätigt …`;
    if (this.loading) return 'Hub-Status wird geladen …';
    if (this.errorCode === 'hub_missing') return 'Kein Hub-Endpunkt konfiguriert.';
    if (this.errorCode === 'conflict') return 'Die Ansicht war veraltet. Status wurde neu geladen.';
    if (this.errorCode === 'forbidden') return 'Der Hub hat diese Aktion nicht autorisiert.';
    if (this.errorCode) return 'Hub-Status konnte nicht sicher gelesen werden.';
    if (!this.selected) return 'Keine Reconciliation-Jobs vorhanden.';
    return `Hub-Status: ${this.selected.state}`;
  }

  private mutate(action: SpeechReconciliationAction, factor?: number): void {
    const job = this.selected;
    const hubUrl = this.resolveHubUrl();
    if (!job || !hubUrl || !this.hubAuthorized || this.pendingAction) return;
    if (
      (action === 'pause' && !this.canPause())
      || (action === 'resume' && !this.canResume())
      || (action === 'cancel' && !this.canCancel())
      || (action === 'reduce' && !this.canReduce())
    ) return;

    const generation = ++this.mutationGeneration;
    const jobId = job.job_id;
    const expectedVersion = job.version;
    const key = this.idempotencyKey(action, jobId, expectedVersion);
    this.pendingAction = action;
    this.errorCode = null;
    this.readGeneration += 1;
    this.readSubscription?.unsubscribe();
    let request: Observable<SpeechReconciliationJobView>;
    try {
      request = action === 'reduce'
        ? this.api.reduce(hubUrl, jobId, expectedVersion, factor as number, key)
        : this.api[action](hubUrl, jobId, expectedVersion, key);
    } catch (error) {
      this.applyMutationError(error, generation);
      return;
    }
    this.mutationSubscription = request.subscribe({
      next: updated => {
        if (generation !== this.mutationGeneration || !this.hubAuthorized) return;
        this.pendingAction = null;
        if (this.selected?.job_id === jobId && this.selected.version === expectedVersion) {
          this.jobs = Object.freeze(this.jobs.map(candidate => candidate.job_id === jobId ? updated : candidate));
          this.selectProjection(updated);
        }
        this.changeDetector.markForCheck();
      },
      error: error => this.applyMutationError(error, generation),
    });
    this.changeDetector.markForCheck();
  }

  private selectProjection(job: SpeechReconciliationJobView | null): void {
    this.selected = job;
    this.reduceFactor = job ? Math.max(1, job.max_compute_factor - 1) : 1;
  }

  private resetProjection(): void {
    this.readSubscription?.unsubscribe();
    this.mutationSubscription?.unsubscribe();
    this.mutationGeneration += 1;
    this.jobs = [];
    this.selectProjection(null);
    this.loading = false;
    this.pendingAction = null;
    this.errorCode = null;
    this.changeDetector.markForCheck();
  }

  private resolveHubUrl(): string {
    const explicit = this.hubUrl.trim();
    if (explicit) return explicit.replace(/\/+$/, '');
    const entries = this.directory.list();
    const entry = entries.find(candidate => candidate.role === 'hub')
      ?? entries.find(candidate => candidate.name === 'hub');
    return (entry?.url || '').trim().replace(/\/+$/, '');
  }

  private armPolling(): void {
    if (this.pollTimer !== null) clearInterval(this.pollTimer);
    const intervalMs = Number.isInteger(this.pollIntervalMs)
      ? Math.min(60_000, Math.max(2_000, this.pollIntervalMs))
      : 5_000;
    this.pollTimer = setInterval(() => this.refresh(), intervalMs);
  }

  private canMutate(): boolean {
    return this.hubAuthorized && !this.loading && !this.pendingAction;
  }

  private actionLabel(action: SpeechReconciliationAction): string {
    return { pause: 'Pause', resume: 'Fortsetzung', cancel: 'Abbruch', reduce: 'Reduktion' }[action];
  }

  private idempotencyKey(action: SpeechReconciliationAction, jobId: string, version: number): string {
    const random = globalThis.crypto?.randomUUID?.()
      ?? `${Date.now()}-${++this.idempotencySerial}`;
    return `speech-reconciliation-${action}-${version}-${jobId.slice(0, 24)}-${random}`.slice(0, 192);
  }

  private httpStatus(error: unknown): number | null {
    if (!error || typeof error !== 'object' || !('status' in error)) return null;
    const status = (error as { status?: unknown }).status;
    return Number.isInteger(status) ? status as number : null;
  }

  private applyMutationError(error: unknown, generation: number): void {
    if (generation !== this.mutationGeneration) return;
    this.pendingAction = null;
    const status = this.httpStatus(error);
    this.errorCode = status === 409 || status === 412
      ? 'conflict'
      : status === 401 || status === 403
        ? 'forbidden'
        : 'request_failed';
    this.changeDetector.markForCheck();
    if (this.errorCode === 'conflict') this.refresh();
  }
}
