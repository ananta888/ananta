import { Component, EventEmitter, Input, OnInit, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ContextAccessPolicyApiService } from '../../services/context-access-policy-api.service';
import { PolicyTemplate } from '../../models/context-access-policy.model';
import { Observable, of } from 'rxjs';
import { catchError } from 'rxjs/operators';

@Component({
  selector: 'app-policy-preset-selector',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="preset-selector-container card shadow-sm">
      <div class="card-header">
        <h5>Sicherheits-Voreinstellungen</h5>
      </div>
      <div class="card-body">
        <p class="text-muted">Wählen Sie eine Vorlage als Basis für Ihre Richtlinie.</p>
        
        <div *ngIf="templates$ | async as templates; else loading" class="list-group">
          <button *ngFor="let tpl of templates" 
                  type="button" 
                  class="list-group-item list-group-item-action flex-column align-items-start"
                  (click)="selectPreset(tpl)">
            <div class="d-flex w-100 justify-content-between">
              <h6 class="mb-1">{{ tpl.name }}</h6>
              <small class="badge" [ngClass]="getRiskClass(tpl.risk_level)">{{ tpl.risk_level }}</small>
            </div>
            <p class="mb-1 small text-secondary">{{ tpl.description }}</p>
            <div class="mt-2">
              <span class="badge rounded-pill bg-light text-dark border me-1" *ngIf="!tpl.cloud_allowed">No Cloud</span>
              <span class="badge rounded-pill bg-info text-white me-1" *ngIf="tpl.cloud_allowed">Cloud Allowed</span>
              <span class="badge rounded-pill bg-danger text-white me-1" *ngIf="tpl.risk_level === 'high' || tpl.risk_level === 'critical'">Risky</span>
            </div>
          </button>
        </div>

        <ng-template #loading>
          <div class="text-center py-3">
            <div class="spinner-border spinner-border-sm text-primary" role="status"></div>
          </div>
        </ng-template>
      </div>
    </div>
  `,
  styles: [`
    .preset-selector-container { max-width: 500px; }
    .badge-low { background-color: #28a745; }
    .badge-medium { background-color: #ffc107; color: #212529; }
    .badge-high { background-color: #fd7e14; }
    .badge-critical { background-color: #dc3545; }
  `]
})
export class PolicyPresetSelectorComponent implements OnInit {
  private api = inject(ContextAccessPolicyApiService);
  
  @Input() baseUrl = '';
  @Output() presetSelected = new EventEmitter<PolicyTemplate>();

  templates$!: Observable<PolicyTemplate[]>;

  ngOnInit(): void {
    this.templates$ = this.api.listTemplates(this.baseUrl).pipe(
      catchError(err => {
        console.error('Fehler beim Laden der Templates', err);
        return of([]);
      })
    );
  }

  selectPreset(tpl: PolicyTemplate): void {
    if (tpl.cloud_allowed || tpl.risk_level === 'high' || tpl.risk_level === 'critical') {
      if (confirm(`Warnung: Das Preset "${tpl.name}" erlaubt Cloud-Zugriff oder hat ein erhöhtes Risiko. Fortfahren?`)) {
        this.presetSelected.emit(tpl);
      }
    } else {
      this.presetSelected.emit(tpl);
    }
  }

  getRiskClass(level: string): string {
    return `badge-${level}`;
  }
}
