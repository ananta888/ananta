import { Injectable, OnDestroy, inject, signal } from '@angular/core';
import { Subscription, catchError, exhaustMap, forkJoin, of, take, timer } from 'rxjs';

import { ModelTrainingApiService } from './model-training-api.service';
import { normalizeTrainingEvent, normalizeTrainingEventPage, normalizeTrainingJob } from './model-training-normalizers';
import { TrainingJobDetail, TrainingJobEvent } from './model-training.models';

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled']);
const MAX_POLL_CYCLES = 201;

@Injectable()
export class ModelTrainingJobMonitorService implements OnDestroy {
  private readonly api = inject(ModelTrainingApiService);
  private subscription?: Subscription;
  private streamSubscription?: Subscription;
  private hubUrl = '';
  private jobId = '';
  private afterSequence = 0;

  readonly job = signal<TrainingJobDetail | null>(null);
  readonly events = signal<TrainingJobEvent[]>([]);
  readonly connected = signal(false);
  readonly mode = signal<'idle' | 'streaming' | 'polling'>('idle');
  readonly error = signal('');

  start(hubUrl: string, jobId: string): void {
    if (this.hubUrl === hubUrl && this.jobId === jobId && this.subscription) return;
    this.stop();
    this.hubUrl = hubUrl;
    this.jobId = jobId;
    this.afterSequence = 0;
    this.job.set(null);
    this.events.set([]);
    this.mode.set('polling');
    this.startStreaming(hubUrl, jobId);
    this.subscription = timer(0, 3000).pipe(
      take(MAX_POLL_CYCLES),
      exhaustMap(() => {
        const events = this.mode() === 'streaming'
          ? of({ items: [], count: 0, next_sequence: this.afterSequence })
          : this.api.listJobEvents(hubUrl, jobId, this.afterSequence, 200);
        return forkJoin({ job: this.api.getJob(hubUrl, jobId), events }).pipe(catchError((error) => {
        this.connected.set(false);
        this.error.set(String(error?.error?.reason_code || error?.message || 'Jobstatus konnte nicht geladen werden'));
        return of(null);
        }));
      }),
    ).subscribe({
      next: result => {
        if (!result) return;
        this.connected.set(true);
        this.error.set('');
        const job = normalizeTrainingJob(result.job);
        const eventPage = normalizeTrainingEventPage(result.events);
        this.job.set(job);
        this.mergeEvents(eventPage.items, eventPage.next_sequence);
        if (TERMINAL_STATUSES.has(String(job.status).toLowerCase())) {
          this.stopSubscriptions();
          this.mode.set('idle');
        }
      },
      complete: () => {
        const status = String(this.job()?.status || '').toLowerCase();
        if (!this.jobId || TERMINAL_STATUSES.has(status)) return;
        this.streamSubscription?.unsubscribe();
        this.streamSubscription = undefined;
        this.subscription = undefined;
        this.connected.set(false);
        this.mode.set('idle');
        this.error.set('Automatische Statusabfrage nach 10 Minuten beendet. Mit „Aktualisieren“ kann sie kontrolliert fortgesetzt werden.');
      },
    });
  }

  refresh(): void {
    if (!this.hubUrl || !this.jobId) return;
    const hubUrl = this.hubUrl;
    const jobId = this.jobId;
    this.stopSubscriptions();
    this.hubUrl = '';
    this.jobId = '';
    this.start(hubUrl, jobId);
  }

  stop(): void {
    this.stopSubscriptions();
    this.hubUrl = '';
    this.jobId = '';
    this.mode.set('idle');
    this.connected.set(false);
  }

  ngOnDestroy(): void {
    this.stop();
  }

  private startStreaming(hubUrl: string, jobId: string): void {
    if (typeof this.api.streamJobEvents !== 'function') return;
    this.mode.set('streaming');
    this.streamSubscription = this.api.streamJobEvents(hubUrl, jobId, this.afterSequence).subscribe({
      next: value => {
        const source = value && typeof value === 'object' ? value as Record<string, unknown> : {};
        if (Array.isArray(source.items) || Array.isArray(source.events)) {
          const page = normalizeTrainingEventPage(value);
          this.mergeEvents(page.items, page.next_sequence);
          return;
        }
        this.mergeEvents([normalizeTrainingEvent(value)]);
      },
      error: () => {
        this.streamSubscription = undefined;
        this.mode.set('polling');
      },
      complete: () => {
        this.streamSubscription = undefined;
        this.mode.set('polling');
      },
    });
  }

  private mergeEvents(incoming: TrainingJobEvent[], nextSequence?: number): void {
    const bySequence = new Map(this.events().map(event => [event.sequence, event]));
    for (const event of incoming) bySequence.set(event.sequence, event);
    const merged = Array.from(bySequence.values()).sort((left, right) => left.sequence - right.sequence).slice(-500);
    this.events.set(merged);
    this.afterSequence = Math.max(
      this.afterSequence,
      Number(nextSequence || 0),
      ...merged.map(event => Number(event.sequence || 0)),
    );
  }

  private stopSubscriptions(): void {
    this.subscription?.unsubscribe();
    this.subscription = undefined;
    this.streamSubscription?.unsubscribe();
    this.streamSubscription = undefined;
  }
}
