import { Component, EventEmitter, Input, OnInit, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ContextAccessPolicyApiService } from '../../services/context-access-policy-api.service';
import { ContextAccessPolicy } from '../../models/context-access-policy.model';
import { Observable, of } from 'rxjs';
import { catchError } from 'rxjs/operators';

@Component({
  selector: 'app-policy-version-list',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="version-list-container card shadow-sm">
      <div class="card-header d-flex justify-content-between align-items-center">
        <h5 class="mb-0">Versionen & Historie</h5>
        <button class="btn btn-sm btn-outline-primary" (click)="loadVersions()">Aktualisieren</button>
      </div>
      <div class="card-body p-0">
        @if (policies$ | async; as policies) {
          <div class="table-responsive">
            <table class="table table-hover mb-0">
              <thead class="table-light">
                <tr>
                  <th>Version</th>
                  <th>Status</th>
                  <th>Erstellt</th>
                  <th>Aktionen</th>
                </tr>
              </thead>
              <tbody>
                @for (p of policies; track p) {
                  <tr [class.table-active]="p.validation_state === 'active'">
                    <td>v{{ p.version }}</td>
                    <td>
                      <span class="badge" [ngClass]="getStatusClass(p.validation_state)">
                        {{ p.validation_state }}
                      </span>
                    </td>
                    <td>{{ p.created_at | date:'short' }}</td>
                    <td>
                      <button class="btn btn-sm btn-link" (click)="viewPolicy(p)">Ansehen</button>
                      @if (p.validation_state !== 'active') {
                        <button class="btn btn-sm btn-link"
                        (click)="activatePolicy(p)">Aktivieren</button>
                      }
                    </td>
                  </tr>
                }
                @if (policies.length === 0) {
                  <tr>
                    <td colspan="4" class="text-center py-3 text-muted">Keine Versionen gefunden.</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        } @else {
          <div class="text-center py-4">
            <div class="spinner-border text-primary" role="status"></div>
          </div>
        }
      </div>
    </div>
    `,
  styles: [`
    .version-list-container { margin-top: 20px; }
    .badge-active { background-color: #28a745; }
    .badge-draft { background-color: #ffc107; color: #212529; }
    .badge-archived { background-color: #6c757d; }
  `]
})
export class PolicyVersionListComponent implements OnInit {
  private api = inject(ContextAccessPolicyApiService);

  @Input() baseUrl = '';
  @Input() projectId = 'default-project';
  @Output() policyViewed = new EventEmitter<ContextAccessPolicy>();

  policies$!: Observable<ContextAccessPolicy[]>;

  ngOnInit(): void {
    this.loadVersions();
  }

  loadVersions(): void {
    this.policies$ = this.api.listPolicies(this.baseUrl, this.projectId).pipe(
      catchError(err => {
        console.error('Fehler beim Laden der Versionen', err);
        return of([]);
      })
    );
  }

  viewPolicy(p: ContextAccessPolicy): void {
    this.policyViewed.emit(p);
  }

  activatePolicy(p: ContextAccessPolicy): void {
    if (confirm(`Soll Version ${p.version} wirklich als aktive Richtlinie gesetzt werden?`)) {
      this.api.activatePolicy(this.baseUrl, p.policy_id).subscribe({
        next: () => this.loadVersions(),
        error: (err) => alert('Fehler bei der Aktivierung: ' + err.message)
      });
    }
  }

  getStatusClass(state?: string): string {
    return `badge-${state || 'draft'}`;
  }
}
