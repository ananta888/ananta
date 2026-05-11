import { Component, EventEmitter, Input, OnInit, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { WorkerRuntimeCandidateApiService } from '../../../services/worker-runtime-candidate-api.service';
import { WorkerRuntimeCandidate, RuntimeTarget } from '../../../models/worker-runtime-target.model';
import { Observable, forkJoin, of } from 'rxjs';
import { catchError, map } from 'rxjs/operators';

@Component({
  selector: 'app-destination-constraint-editor',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="destination-editor card shadow-sm mb-3">
      <div class="card-header bg-light">
        <h6 class="mb-0">Ziel-Einschränkungen (Destinations)</h6>
      </div>
      <div class="card-body">
        <div class="row g-3">
          <div class="col-md-6">
            <label class="form-label fw-bold">Erlaubte Worker-Typen</label>
            <div class="d-flex flex-wrap gap-2">
              <div *ngFor="let kind of allWorkerKinds" class="form-check">
                <input class="form-check-input" type="checkbox" 
                       [id]="'worker-' + kind"
                       [checked]="allowedWorkerKinds.includes(kind)"
                       (change)="toggleWorkerKind(kind)">
                <label class="form-check-label small" [for]="'worker-' + kind">{{ kind }}</label>
              </div>
            </div>
          </div>

          <div class="col-md-6">
            <label class="form-label fw-bold">Erlaubte Runtime-Umgebungen</label>
            <div class="d-flex flex-wrap gap-2">
              <div *ngFor="let kind of allRuntimeKinds" class="form-check">
                <input class="form-check-input" type="checkbox" 
                       [id]="'runtime-' + kind"
                       [checked]="allowedRuntimeKinds.includes(kind)"
                       (change)="toggleRuntimeKind(kind)">
                <label class="form-check-label small" [for]="'runtime-' + kind">{{ kind }}</label>
              </div>
            </div>
          </div>
        </div>

        <div class="mt-4">
          <label class="form-label fw-bold">Spezifische Worker-Instanzen</label>
          <select class="form-select form-select-sm" multiple [(ngModel)]="allowedWorkerIds" (change)="emit()">
            <option *ngFor="let w of workerCandidates" [value]="w.id">
              {{ w.display_name }} ({{ w.kind }})
            </option>
          </select>
          <small class="text-muted">Keine Auswahl bedeutet: Alle Worker des erlaubten Typs sind zulässig.</small>
        </div>

        <div *ngIf="hasRiskyDestinations" class="alert alert-warning mt-3 mb-0 small">
          <i class="bi bi-exclamation-triangle me-1"></i>
          <strong>Cloud/Externe Ziele:</strong> Diese Regel erlaubt den Versand an Cloud-Ziele oder externe Worker.
        </div>
      </div>
    </div>
  `,
  styles: [`
    .gap-2 { gap: 0.5rem; }
  `]
})
export class DestinationConstraintEditorComponent implements OnInit {
  private api = inject(WorkerRuntimeCandidateApiService);

  @Input() allowedWorkerKinds: string[] = [];
  @Input() allowedRuntimeKinds: string[] = [];
  @Input() allowedWorkerIds: string[] = [];
  @Output() changed = new EventEmitter<any>();

  workerCandidates: WorkerRuntimeCandidate[] = [];
  runtimeTargets: RuntimeTarget[] = [];

  allWorkerKinds: string[] = ['native_ananta_worker', 'opencode', 'hermes', 'shellgpt', 'remote_worker'];
  allRuntimeKinds: string[] = ['local_process', 'docker_container', 'wsl', 'cloud_worker', 'remote_http_worker'];

  ngOnInit(): void {
    forkJoin({
      workers: this.api.listWorkerCandidates('').pipe(catchError(() => of([]))),
      runtimes: this.api.listRuntimeTargets('').pipe(catchError(() => of([])))
    }).subscribe(res => {
      this.workerCandidates = res.workers;
      this.runtimeTargets = res.runtimes;
    });
  }

  toggleWorkerKind(kind: string): void {
    if (this.allowedWorkerKinds.includes(kind)) {
      this.allowedWorkerKinds = this.allowedWorkerKinds.filter(k => k !== kind);
    } else {
      this.allowedWorkerKinds = [...this.allowedWorkerKinds, kind];
    }
    this.emit();
  }

  toggleRuntimeKind(kind: string): void {
    if (this.allowedRuntimeKinds.includes(kind)) {
      this.allowedRuntimeKinds = this.allowedRuntimeKinds.filter(k => k !== kind);
    } else {
      this.allowedRuntimeKinds = [...this.allowedRuntimeKinds, kind];
    }
    this.emit();
  }

  get hasRiskyDestinations(): boolean {
    return this.allowedRuntimeKinds.includes('cloud_worker') || 
           this.allowedWorkerKinds.includes('remote_worker');
  }

  emit(): void {
    this.changed.emit({
      allowed_worker_kinds: this.allowedWorkerKinds,
      allowed_runtime_kinds: this.allowedRuntimeKinds,
      allowed_worker_ids: this.allowedWorkerIds
    });
  }
}
