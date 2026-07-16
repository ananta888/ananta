import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { TableShellComponent } from '../../../shared/ui/display';
import { FormFieldComponent } from '../../../shared/ui/forms';
import { SectionCardComponent } from '../../../shared/ui/layout';
import { EmptyStateComponent, StatusBadgeComponent } from '../../../shared/ui/state';
import { ModelTrainingFacade } from '../model-training.facade';
import { EvaluationScorerName } from '../model-training.models';
import { apiErrorMessage, boundedText, idempotencyKey, trainingStatusTone } from '../model-training-status';

@Component({
  selector: 'app-model-training-evaluation-panel',
  standalone: true,
  imports: [FormsModule, EmptyStateComponent, FormFieldComponent, SectionCardComponent, StatusBadgeComponent, TableShellComponent],
  template: `
    @if (facade.selectedAdapter(); as adapter) {
      <app-section-card title="Base-vs.-Adapter-Evaluation" [subtitle]="adapter.name + ' gegen ' + adapter.base_model_id" variant="primary">
        <div class="evaluation-controls">
          <app-form-field label="Validation-Dataset" [required]="true">
            <select [(ngModel)]="datasetId">
              <option value="">Bitte wählen</option>
              @for (dataset of eligibleDatasets(); track dataset.id) { <option [value]="dataset.id">{{ dataset.name }} · {{ dataset.validation_record_count }} Validation-Records</option> }
            </select>
          </app-form-field>
          <app-form-field label="Scorer" [required]="true">
            <select [(ngModel)]="scorerName">
              <option value="generic">Generisch</option>
              <option value="ananta_todo_json">Ananta Todo-JSON</option>
            </select>
          </app-form-field>
          @if (liveRuntime()) {
            <app-form-field label="Begründung für Live-Evaluation" [required]="true">
              <textarea [(ngModel)]="riskReason" rows="2" maxlength="500"></textarea>
            </app-form-field>
            <label class="live-confirmation">
              <input type="checkbox" [(ngModel)]="liveConfirmed" /> Live-Ausführung bestätigen
            </label>
          }
          <button type="button" (click)="evaluate()" [disabled]="!canEvaluate()">Evaluation starten</button>
          @if (facade.selectedEvaluation()?.id) { <button type="button" class="secondary" (click)="facade.loadEvaluation(facade.selectedEvaluation()!.id)">Report aktualisieren</button> }
        </div>
        @if (busy) { <p role="status" aria-live="polite">Evaluation wird vom Hub angenommen …</p> }
        @if (error) { <div class="state-banner error" role="alert">{{ error }}</div> }

        @if (facade.selectedEvaluation(); as report) {
          <div class="row gap-sm wrap mt-sm">
            <app-status-badge [label]="report.status" [tone]="tone(report.status)" [dot]="true" />
            @if (report.passed !== undefined) { <app-status-badge [label]="report.passed ? 'Gate bestanden' : 'Gate nicht bestanden'" [tone]="report.passed ? 'success' : 'error'" /> }
            <span class="badge">Score {{ report.aggregate_score ?? '-' }}</span>
          </div>
          <app-table-shell title="Aggregierte Metriken" [empty]="report.metrics.length === 0" emptyTitle="Noch keine Metriken">
            <table class="standard-table table-min-600"><thead><tr><th>Metrik</th><th>Base</th><th>Adapter</th><th>Delta</th><th>Threshold</th><th>Gate</th></tr></thead>
              <tbody>@for (metric of report.metrics; track metric.name) {
                <tr><td>{{ metric.name }}</td><td>{{ metric.base_value }}</td><td>{{ metric.adapter_value }}</td><td>{{ metric.delta }}</td><td>{{ metric.threshold ?? '-' }}</td><td>{{ metric.passed === undefined ? '-' : (metric.passed ? 'bestanden' : 'nicht bestanden') }}</td></tr>
              }</tbody>
            </table>
          </app-table-shell>
          <app-table-shell title="Begrenzte Sample-Gegenüberstellung" [empty]="report.samples.length === 0" emptyTitle="Noch keine Samples">
            <table class="standard-table table-min-600"><thead><tr><th>Sample</th><th>Base-Ausgabe</th><th>Adapter-Ausgabe</th><th>Erwartet</th><th>Gewinner</th></tr></thead>
              <tbody>@for (sample of report.samples; track sample.id || sample.record_index) {
                <tr><td>{{ sample.id || sample.record_index }}</td><td class="sample-output">{{ text(sample.base_output) }}</td><td class="sample-output">{{ text(sample.adapter_output) }}</td><td class="sample-output">{{ text(sample.expected_output) }}</td><td>{{ sample.winner || '-' }}</td></tr>
              }</tbody>
            </table>
          </app-table-shell>
        }
      </app-section-card>
    } @else {
      <app-empty-state title="Adapter auswählen" description="Wählen Sie einen Registry-Eintrag, um ihn gegen ein Validation-Dataset zu evaluieren." [compact]="true" />
    }
  `,
  styles: [`
    .evaluation-controls { display:grid; grid-template-columns:2fr 1fr minmax(220px,1fr) auto auto; gap:8px; align-items:end; }
    .live-confirmation { display:flex; gap:8px; align-items:center; min-height:40px; }
    .sample-output { max-width:42ch; white-space:pre-wrap; overflow-wrap:anywhere; }
    @media (max-width:700px) { .evaluation-controls { grid-template-columns:1fr; } }
  `],
})
export class EvaluationPanelComponent {
  readonly facade = inject(ModelTrainingFacade);
  readonly tone = trainingStatusTone;
  readonly text = (value: unknown) => boundedText(value, 1200);
  datasetId = '';
  scorerName: EvaluationScorerName = 'generic';
  riskReason = '';
  liveConfirmed = false;
  busy = false;
  error = '';

  eligibleDatasets() {
    return this.facade.datasets().filter(dataset => dataset.validation_record_count > 0 && (dataset.trainable || dataset.validation_status === 'valid' || dataset.status === 'valid'));
  }

  liveRuntime(): boolean { return this.facade.capabilities()?.mode === 'live'; }

  canEvaluate(): boolean {
    return !this.busy && Boolean(this.datasetId)
      && (!this.liveRuntime() || (this.liveConfirmed && this.riskReason.trim().length >= 8));
  }

  evaluate(): void {
    const adapter = this.facade.selectedAdapter();
    if (!adapter || !this.canEvaluate()) return;
    this.busy = true;
    this.error = '';
    this.facade.evaluateAdapter(
      adapter.id,
      this.datasetId,
      this.scorerName,
      this.liveConfirmed,
      this.riskReason,
      idempotencyKey('adapter-evaluation'),
    ).pipe(finalize(() => this.busy = false)).subscribe({
      error: error => this.error = apiErrorMessage(error, 'Evaluation konnte nicht gestartet werden.'),
    });
  }
}
