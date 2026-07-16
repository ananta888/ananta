import { CommonModule } from '@angular/common';
import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { MetricCardComponent, TableShellComponent } from '../../../shared/ui/display';
import { FormFieldComponent } from '../../../shared/ui/forms';
import { SectionCardComponent } from '../../../shared/ui/layout';
import { EmptyStateComponent, StatusBadgeComponent } from '../../../shared/ui/state';
import { ModelTrainingFacade } from '../model-training.facade';
import { TrainingJobDetail, TrainingJobEvent, TrainingMetric } from '../model-training.models';
import { apiErrorMessage, boundedText, boundedTrainingLog, idempotencyKey, trainingStatusTone } from '../model-training-status';
import { TrainingMetricsChartComponent } from './training-metrics-chart.component';

const TERMINAL = new Set(['completed', 'failed', 'cancelled']);

@Component({
  selector: 'app-model-training-job-detail',
  standalone: true,
  imports: [CommonModule, FormsModule, EmptyStateComponent, FormFieldComponent, MetricCardComponent, SectionCardComponent, StatusBadgeComponent, TableShellComponent, TrainingMetricsChartComponent],
  template: `
    @if (facade.monitor.job(); as job) {
      <app-section-card [title]="'Job ' + job.id" [subtitle]="job.dataset_name || job.dataset_id" variant="technical">
        <div section-actions>
          <app-status-badge [label]="job.status" [tone]="tone(job.status)" [dot]="true" />
          <span class="badge">
            {{ facade.monitor.mode() === 'streaming' ? 'SSE + Cursor-Fallback' : (facade.monitor.mode() === 'polling' ? 'Cursor-Polling' : 'inaktiv') }}
          </span>
          <button type="button" class="secondary btn-small" (click)="facade.monitor.refresh()">Aktualisieren</button>
        </div>

        <div class="grid cols-4 job-metrics">
          <app-metric-card label="Phase" [value]="job.phase || '-'" />
          <app-metric-card label="Step" [value]="(job.current_step ?? 0) + ' / ' + (job.max_steps ?? '?')" />
          <app-metric-card label="Train Loss" [value]="job.latest_train_loss ?? '-'" />
          <app-metric-card label="Eval Loss" [value]="job.latest_eval_loss ?? '-'" />
        </div>

        <div class="progress-row" role="status" aria-live="polite">
          <progress [value]="progress(job.progress_percent)" max="100" [attr.aria-label]="'Trainingsfortschritt ' + progress(job.progress_percent) + ' Prozent'"></progress>
          <strong>{{ progress(job.progress_percent) }} %</strong>
        </div>

        @if (metrics().length) {
          <app-training-metrics-chart [metrics]="metrics()" />
          <app-table-shell title="Metrikwerte" subtitle="Tabellarische Alternative zur Verlaufsgrafik.">
            <table class="standard-table table-min-600">
              <thead><tr><th>Step</th><th>Epoch</th><th>Train Loss</th><th>Eval Loss</th><th>Learning Rate</th><th>GPU-Speicher</th></tr></thead>
              <tbody>@for (metric of metrics(); track metric.step) {
                <tr><td>{{ metric.step }} / {{ metric.max_steps || job.max_steps || '?' }}</td><td>{{ metric.epoch ?? '-' }}</td><td>{{ metric.train_loss ?? '-' }}</td><td>{{ metric.eval_loss ?? '-' }}</td><td>{{ metric.learning_rate ?? '-' }}</td><td>{{ metric.gpu_memory_bytes ? (metric.gpu_memory_bytes | number) : '-' }}</td></tr>
              }</tbody>
            </table>
          </app-table-shell>
        }

        @if (job.error) {
          <div class="state-banner error" role="alert"><strong>{{ safe(job.error.code) }}</strong> · {{ safe(job.error.message) }}{{ job.error.retriable ? ' · wiederholbar' : '' }}</div>
        }
        @if (facade.monitor.error()) { <div class="state-banner warning" role="alert">{{ facade.monitor.error() }}</div> }

        @if (cancellationLabel(job); as cancellation) {
          <p class="state-banner warning" role="status"><strong>Abbruchstatus:</strong> {{ cancellation }}</p>
        }

        @if (canCancel()) {
          @if (!confirmCancel) {
            <button type="button" class="danger-button" (click)="confirmCancel = true">Job abbrechen</button>
          } @else {
            <div class="cancel-panel">
              <app-form-field label="Abbruchgrund" [required]="true">
                <input [(ngModel)]="cancelReason" maxlength="500" />
              </app-form-field>
              <button type="button" class="danger-button" (click)="cancel()" [disabled]="cancelBusy || cancelReason.trim().length < 4">Abbruch bestätigen</button>
              <button type="button" class="secondary" (click)="confirmCancel = false" [disabled]="cancelBusy">Zurück</button>
            </div>
          }
        } @else if (job.status === 'cancel_requested') {
          <p role="status">Abbruch wurde angefragt; der Job gilt erst nach Worker-Bestätigung oder gefencetem Abschluss als abgebrochen.</p>
        }
        @if (actionError) { <div class="state-banner error" role="alert">{{ actionError }}</div> }
      </app-section-card>

      <app-table-shell
        title="Redigierte Jobereignisse und Logs"
        subtitle="Maximal 500 sequenzielle Events; Inhalte werden zusätzlich clientseitig begrenzt."
        [empty]="facade.monitor.events().length === 0"
        emptyTitle="Noch keine Jobereignisse">
        <div table-toolbar><button type="button" class="secondary btn-small" (click)="copyLogs()" [disabled]="!facade.monitor.events().length">Logs kopieren</button></div>
        <table class="standard-table table-min-600" data-testid="training-job-events">
          <thead><tr><th>Sequenz</th><th>Zeit</th><th>Typ</th><th>Phase</th><th>Meldung</th></tr></thead>
          <tbody>@for (event of facade.monitor.events(); track event.sequence) {
            <tr><td>{{ event.sequence }}</td><td>{{ event.timestamp ? (event.timestamp * 1000 | date:'mediumTime') : '-' }}</td><td>{{ event.event_type }}</td><td>{{ event.phase || '-' }}</td><td class="event-message">{{ eventMessage(event) }}</td></tr>
          }</tbody>
        </table>
      </app-table-shell>
    } @else {
      <app-empty-state title="Trainingsjob auswählen" description="Wählen Sie einen Job aus der Hub-Historie, um Fortschritt, Metriken und Events zu sehen." [compact]="true" />
    }
  `,
  styles: [`
    .job-metrics { margin-bottom:12px; }
    .progress-row { display:flex; align-items:center; gap:10px; margin-bottom:14px; }
    .progress-row progress { width:min(640px,100%); }
    .cancel-panel { display:flex; gap:8px; align-items:end; margin-top:12px; }
    .cancel-panel app-form-field { flex:1; }
    .danger-button { border-color:var(--tone-error); color:var(--tone-error-text); background:transparent; }
    .event-message { max-width:70ch; white-space:pre-wrap; overflow-wrap:anywhere; }
    @media (max-width:700px) { .job-metrics { grid-template-columns:1fr; } .cancel-panel { align-items:stretch; flex-direction:column; } }
  `],
})
export class TrainingJobDetailComponent {
  readonly facade = inject(ModelTrainingFacade);
  readonly tone = trainingStatusTone;
  readonly safe = (value: unknown) => boundedText(value, 700);
  confirmCancel = false;
  cancelReason = '';
  cancelBusy = false;
  actionError = '';

  progress(value: number | undefined): number { return Math.max(0, Math.min(100, Number(value || 0))); }

  metrics(): TrainingMetric[] {
    const values = [...(this.facade.monitor.job()?.metrics || [])];
    for (const event of this.facade.monitor.events()) if (event.metric) values.push(event.metric);
    const byStep = new Map<number, TrainingMetric>();
    for (const metric of values) byStep.set(Number(metric.step), { ...(byStep.get(Number(metric.step)) || {} as TrainingMetric), ...metric });
    return Array.from(byStep.values()).sort((left, right) => left.step - right.step).slice(-500);
  }

  canCancel(): boolean {
    const job = this.facade.monitor.job();
    return Boolean(job && job.status !== 'cancel_requested' && !TERMINAL.has(String(job.status).toLowerCase()) && job.cancellable !== false);
  }

  cancellationLabel(job: TrainingJobDetail): string {
    if (job.status === 'cancel_requested') return 'angefragt; Worker-Bestätigung oder gefenceter Forced-Termination-Abschluss steht aus.';
    if (job.status !== 'cancelled') return '';
    return job.cancel_mode === 'forced'
      ? 'erzwungen und gefencet abgeschlossen.'
      : 'kooperativ vom Worker bestätigt.';
  }

  cancel(): void {
    const job = this.facade.monitor.job();
    if (!job || !this.canCancel() || this.cancelBusy || this.cancelReason.trim().length < 4) return;
    this.cancelBusy = true;
    this.actionError = '';
    this.facade.cancelJob(job.id, this.cancelReason, idempotencyKey('training-cancel')).pipe(finalize(() => this.cancelBusy = false)).subscribe({
      next: () => { this.confirmCancel = false; this.cancelReason = ''; },
      error: error => this.actionError = apiErrorMessage(error, 'Abbruch konnte nicht angefragt werden.'),
    });
  }

  eventMessage(event: TrainingJobEvent): string {
    return boundedTrainingLog(event.message || event.reason_code || event.metric && JSON.stringify(event.metric) || '-', 700);
  }

  async copyLogs(): Promise<void> {
    const text = this.facade.monitor.events().map(event => `${event.sequence}\t${event.event_type}\t${this.eventMessage(event)}`).join('\n');
    try { await navigator.clipboard.writeText(text); } catch { this.actionError = 'Logs konnten nicht in die Zwischenablage kopiert werden.'; }
  }
}
