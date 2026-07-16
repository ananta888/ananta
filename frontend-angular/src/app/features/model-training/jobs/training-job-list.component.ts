import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Output, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { TableShellComponent } from '../../../shared/ui/display';
import { StatusBadgeComponent } from '../../../shared/ui/state';
import { ModelTrainingFacade } from '../model-training.facade';
import { TrainingJobSummary } from '../model-training.models';
import { trainingStatusTone } from '../model-training-status';

@Component({
  selector: 'app-model-training-job-list',
  standalone: true,
  imports: [CommonModule, FormsModule, StatusBadgeComponent, TableShellComponent],
  template: `
    <app-table-shell
      title="Trainingsjobs"
      subtitle="Persistente Hub-Queue und Jobhistorie."
      [loading]="facade.loadingJobs()"
      [empty]="!facade.loadingJobs() && facade.jobs().length === 0"
      loadingLabel="Trainingsjobs werden geladen"
      emptyTitle="Noch keine Trainingsjobs"
      refreshLabel="Aktualisieren"
      (refresh)="load()">
      <div table-toolbar class="job-filters">
        <select [(ngModel)]="status" aria-label="Jobstatus">
          <option value="">Alle Status</option>
          <option value="queued">queued</option><option value="running">running</option>
          <option value="completed">completed</option><option value="failed">failed</option>
          <option value="cancelled">cancelled</option>
        </select>
        <select [(ngModel)]="backend" aria-label="Training-Backend">
          <option value="">Alle Backends</option>
          @for (item of facade.capabilities()?.backends || []; track item.id) { <option [value]="item.id">{{ item.id }}</option> }
        </select>
        <select [(ngModel)]="datasetId" aria-label="Job-Dataset">
          <option value="">Alle Datasets</option>
          @for (dataset of facade.datasets(); track dataset.id) { <option [value]="dataset.id">{{ dataset.name }}</option> }
        </select>
        <button type="button" class="secondary btn-small" (click)="load()">Filtern</button>
      </div>
      <table class="standard-table table-min-600" data-testid="training-job-table">
        <thead><tr><th>Job</th><th>Status / Phase</th><th>Dataset</th><th>Backend</th><th>Queue</th><th>Fortschritt</th><th>Erstellt</th></tr></thead>
        <tbody>
          @for (job of facade.jobs(); track job.id) {
            <tr class="job-row">
              <td>
                <button
                  type="button"
                  class="table-entity-button"
                  [attr.aria-current]="facade.monitor.job()?.id === job.id ? 'true' : null"
                  [attr.aria-label]="'Trainingsjob ' + job.id + ' öffnen'"
                  (click)="select(job)">
                  <strong>{{ job.id }}</strong><span class="muted font-sm">{{ job.base_model_id }}</span>
                </button>
              </td>
              <td><app-status-badge [label]="job.status" [tone]="tone(job.status)" [dot]="true" /><div class="muted font-sm">{{ job.phase || '-' }}</div></td>
              <td>{{ job.dataset_name || job.dataset_id }}</td>
              <td>{{ job.backend }}</td>
              <td>{{ job.queue_position ?? '-' }}</td>
              <td>{{ job.progress_percent ?? 0 }} % · {{ job.current_step ?? 0 }}/{{ job.max_steps ?? '?' }}</td>
              <td>{{ job.created_at ? (job.created_at * 1000 | date:'short') : '-' }}</td>
            </tr>
          }
        </tbody>
      </table>
    </app-table-shell>
  `,
  styles: [`
    .job-filters { display:flex; flex-wrap:wrap; gap:6px; }
    .job-filters select { width:auto; min-width:130px; }
    .job-row:hover { background:color-mix(in srgb,var(--accent) 7%,transparent); }
    .table-entity-button { display:grid; gap:2px; width:100%; padding:0; border:0; background:transparent; color:inherit; text-align:left; }
  `],
})
export class TrainingJobListComponent {
  readonly facade = inject(ModelTrainingFacade);
  @Output() selected = new EventEmitter<TrainingJobSummary>();
  status = '';
  backend = '';
  datasetId = '';
  readonly tone = trainingStatusTone;

  load(): void { this.facade.loadJobs({ status: this.status, backend: this.backend, dataset_id: this.datasetId }); }
  select(job: TrainingJobSummary): void { this.facade.selectJob(job.id); this.selected.emit(job); }
}
