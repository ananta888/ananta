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
        <h6 class="mb-0">Ziel-Einschränkungen (Destinations & Capabilities)</h6>
      </div>
      <div class="card-body">
        <div class="row g-3">
          <div class="col-md-6">
            <label class="form-label fw-bold small">Erlaubte Worker-Typen</label>
            <div class="d-flex flex-wrap gap-2">
              <div *ngFor="let kind of allWorkerKinds" class="form-check">
                <input class="form-check-input" type="checkbox" 
                       [id]="'worker-' + kind"
                       [checked]="allowedWorkerKinds.includes(kind)"
                       (change)="toggleWorkerKind(kind)">
                <label class="form-check-label small" [for]="'worker-' + kind">
                  {{ kind }}
                  <i *ngIf="kind === 'remote_worker'" class="bi bi-cloud text-warning ms-1" title="Externer/Cloud Worker"></i>
                </label>
              </div>
            </div>
          </div>

          <div class="col-md-6">
            <label class="form-label fw-bold small">Erlaubte Runtime-Umgebungen</label>
            <div class="d-flex flex-wrap gap-2">
              <div *ngFor="let kind of allRuntimeKinds" class="form-check">
                <input class="form-check-input" type="checkbox" 
                       [id]="'runtime-' + kind"
                       [checked]="allowedRuntimeKinds.includes(kind)"
                       (change)="toggleRuntimeKind(kind)">
                <label class="form-check-label small" [for]="'runtime-' + kind">
                  {{ kind }}
                  <i *ngIf="kind === 'cloud_worker'" class="bi bi-cloud text-warning ms-1" title="Cloud Runtime"></i>
                </label>
              </div>
            </div>
          </div>
        </div>

        <hr>

        <div class="row g-3">
          <div class="col-md-6">
            <label class="form-label fw-bold small">Required Capabilities (T016)</label>
            <div class="d-flex flex-wrap gap-2">
              <div *ngFor="let cap of allCapabilities" class="form-check">
                <input class="form-check-input" type="checkbox" 
                       [id]="'cap-' + cap"
                       [checked]="destinationCapabilities.includes(cap)"
                       (change)="toggleCapability(cap)">
                <label class="form-check-label small" [for]="'cap-' + cap">{{ cap }}</label>
              </div>
            </div>
          </div>
          <div class="col-md-6">
             <label class="form-label fw-bold small">Spezifische Worker-Instanzen</label>
             <select class="form-select form-select-sm" multiple [(ngModel)]="allowedWorkerIds" (change)="emit()">
               <option *ngFor="let w of workerCandidates" [value]="w.id">
                 {{ w.display_name }} ({{ w.kind }})
               </option>
             </select>
          </div>
        </div>

        <div *ngIf="hasRiskyDestinations" class="alert alert-warning mt-3 mb-0 p-2 small">
          <i class="bi bi-exclamation-triangle-fill me-1"></i>
          <strong>Cloud/Externe Ziele aktiv:</strong> Diese Regel erlaubt den Versand an Ziele außerhalb der lokalen Kontrolle.
        </div>
      </div>
    </div>
  `,
  styles: [`
    .gap-2 { gap: 0.5rem; }
    .destination-editor { border-left: 4px solid #0dcaf0; }
  `]
})
export class DestinationConstraintEditorComponent implements OnInit {
  private api = inject(WorkerRuntimeCandidateApiService);

  @Input() allowedWorkerKinds: string[] = [];
  @Input() allowedRuntimeKinds: string[] = [];
  @Input() allowedWorkerIds: string[] = [];
  @Input() destinationCapabilities: string[] = [];
  @Output() changed = new EventEmitter<any>();

  workerCandidates: WorkerRuntimeCandidate[] = [];
  runtimeTargets: RuntimeTarget[] = [];

  allWorkerKinds: string[] = ['native_ananta_worker', 'opencode', 'hermes', 'shellgpt', 'remote_worker'];
  allRuntimeKinds: string[] = ['local_process', 'docker_container', 'wsl', 'cloud_worker', 'remote_http_worker'];
  allCapabilities: string[] = ['network_access', 'gpu_access', 'high_memory', 'persistent_storage', 'root_allowed'];

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

  toggleCapability(cap: string): void {
    if (this.destinationCapabilities.includes(cap)) {
      this.destinationCapabilities = this.destinationCapabilities.filter(c => c !== cap);
    } else {
      this.destinationCapabilities = [...this.destinationCapabilities, cap];
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
      allowed_worker_ids: this.allowedWorkerIds,
      destination_capabilities: this.destinationCapabilities
    });
  }
}
