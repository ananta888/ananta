import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Output, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { TableShellComponent } from '../../../shared/ui/display';
import { StatusBadgeComponent } from '../../../shared/ui/state';
import { ModelTrainingFacade } from '../model-training.facade';
import { DatasetSummary } from '../model-training.models';
import { shortHash, trainingStatusTone } from '../model-training-status';

@Component({
  selector: 'app-model-training-dataset-catalog',
  standalone: true,
  imports: [CommonModule, FormsModule, StatusBadgeComponent, TableShellComponent],
  template: `
    <app-table-shell
      title="Dataset-Katalog"
      subtitle="Hub-gebundene, versionierte Trainingsdaten ohne lokale Serverpfade."
      [loading]="facade.loadingDatasets()"
      [empty]="!facade.loadingDatasets() && facade.datasets().length === 0"
      loadingLabel="Datasets werden geladen"
      emptyTitle="Noch keine Datasets"
      emptyDescription="Importieren Sie eine kuratierte JSON- oder JSONL-Datei."
      refreshLabel="Aktualisieren"
      (refresh)="load()">
      <div table-toolbar class="dataset-filters">
        <input [(ngModel)]="query" placeholder="Suchen" aria-label="Datasets suchen" (keydown.enter)="load()" />
        <select [(ngModel)]="status" aria-label="Dataset-Status">
          <option value="">Alle Status</option>
          <option value="valid">valid</option>
          <option value="invalid">invalid</option>
          <option value="validating">validating</option>
          <option value="failed">failed</option>
        </select>
        <select [(ngModel)]="format" aria-label="Dataset-Format">
          <option value="">Alle Formate</option>
          <option value="instruction">instruction</option>
          <option value="chat">chat</option>
        </select>
        <button type="button" class="secondary btn-small" (click)="load()">Filtern</button>
      </div>
      <table class="standard-table table-min-600" data-testid="training-dataset-table">
        <thead><tr><th>Name</th><th>Status</th><th>Format</th><th>Records</th><th>Train / Val</th><th>Bytes</th><th>SHA-256</th></tr></thead>
        <tbody>
          @for (dataset of facade.datasets(); track dataset.id) {
            <tr
              class="dataset-row"
              [class.selected]="facade.selectedDataset()?.id === dataset.id">
              <td>
                <button
                  type="button"
                  class="table-entity-button"
                  [attr.aria-current]="facade.selectedDataset()?.id === dataset.id ? 'true' : null"
                  [attr.aria-label]="'Dataset ' + dataset.name + ' öffnen'"
                  (click)="select(dataset)">
                  <strong>{{ dataset.name }}</strong><span class="muted font-sm">{{ dataset.purpose || dataset.id }}</span>
                </button>
              </td>
              <td><app-status-badge [label]="dataset.validation_status || dataset.status" [tone]="tone(dataset.validation_status || dataset.status)" [dot]="true" /></td>
              <td>{{ dataset.format }}</td>
              <td>{{ dataset.record_count }}</td>
              <td>{{ dataset.train_record_count }} / {{ dataset.validation_record_count }}</td>
              <td>{{ dataset.size_bytes | number }}</td>
              <td class="font-mono font-sm">{{ hash(dataset.sha256) }}</td>
            </tr>
          }
        </tbody>
      </table>
    </app-table-shell>
  `,
  styles: [`
    .dataset-filters { display:flex; gap:6px; flex-wrap:wrap; }
    .dataset-filters input,.dataset-filters select { width:auto; min-width:120px; }
    .dataset-row.selected { background:color-mix(in srgb,var(--accent) 10%,transparent); }
    .table-entity-button { display:grid; gap:2px; width:100%; padding:0; border:0; background:transparent; color:inherit; text-align:left; }
  `],
})
export class DatasetCatalogComponent {
  readonly facade = inject(ModelTrainingFacade);
  @Output() selected = new EventEmitter<DatasetSummary>();

  query = '';
  status = '';
  format = '';
  readonly tone = trainingStatusTone;
  readonly hash = shortHash;

  load(): void {
    this.facade.loadDatasets({ q: this.query.trim(), status: this.status, format: this.format });
  }

  select(dataset: DatasetSummary): void {
    this.facade.selectDataset(dataset.id);
    this.selected.emit(dataset);
  }
}
