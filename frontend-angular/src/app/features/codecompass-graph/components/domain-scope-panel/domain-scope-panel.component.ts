import { Component, ChangeDetectionStrategy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { BehaviorSubject } from 'rxjs';
import { DomainScopeService } from '../../services/domain-scope.service';
import { DetectedDomain, ResolvedDomainScopePreview } from '../../models/domain-scope.model';

// CCRDS-015: shows detected domains (id, display name, confidence,
// root paths, boundary warnings) and lets the user set one as the active
// runtime scope. Selection is written into chat_retrieval_domain_hint as
// `domain:<id>`; the preview shows the resolved allowed paths.
@Component({
  standalone: true,
  selector: 'app-domain-scope-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CommonModule],
  template: `
    <div class="domain-scope-panel">
      @if ((svc.scopeEnabled$ | async) === false) {
        <p class="hint">Runtime-Domain-Scope ist deaktiviert (CODECOMPASS_DOMAIN_SCOPE_ENABLED).
          Eine Auswahl wirkt dann nur als weicher Profil-Hinweis.</p>
      }
      @for (err of svc.listErrors$ | async; track err) {
        <p class="warning">{{ err }}</p>
      }
      <ul class="domain-list">
        @for (domain of svc.domains$ | async; track domain.domain_id) {
          <li class="domain-row" [class.selected]="domain.domain_id === selectedId">
            <div class="domain-head">
              <strong>{{ domain.display_name }}</strong>
              <code>{{ domain.domain_id }}</code>
              <span class="confidence" [class.low]="domain.confidence < 0.5">
                {{ domain.confidence | percent }}
              </span>
              @if (domain.has_descriptor) { <span class="badge">descriptor</span> }
            </div>
            @if (domain.confidence < 0.5) {
              <p class="warning">Niedrige Confidence — Scope-Auswahl pruefen.</p>
            }
            @if (domain.boundary_warnings.length) {
              <p class="warning">{{ domain.boundary_warnings.length }} Boundary-Warnung(en)</p>
            }
            <ul class="paths">
              @for (path of domain.root_paths; track path) { <li><code>{{ path }}</code></li> }
            </ul>
            <button (click)="select(domain)">
              {{ domain.domain_id === selectedId ? 'Scope aktiv' : 'Als Scope setzen' }}
            </button>
          </li>
        }
      </ul>
      @if (selectedId) {
        <button class="clear" (click)="clear()">Scope entfernen</button>
      }
      @if (preview$ | async; as preview) {
        <div class="preview">
          <h4>Scope-Preview</h4>
          <p>Erlaubte Lese-Pfade:</p>
          <ul>
            @for (path of preview.allowed_read_paths; track path) { <li><code>{{ path }}</code></li> }
          </ul>
          @for (warning of preview.warnings; track warning) {
            <p class="warning">{{ warning }}</p>
          }
          @for (violation of preview.violations; track violation.message) {
            <p class="violation">{{ violation.kind }}: {{ violation.message }}</p>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    .domain-scope-panel { font-size: 0.85rem; }
    .domain-row { border-bottom: 1px solid #444; padding: 0.4rem 0; list-style: none; }
    .domain-row.selected { background: rgba(80, 160, 255, 0.12); }
    .domain-head { display: flex; gap: 0.5rem; align-items: baseline; }
    .confidence.low { color: #e0a030; }
    .badge { background: #335; border-radius: 3px; padding: 0 0.3rem; }
    .warning { color: #e0a030; margin: 0.15rem 0; }
    .violation { color: #e05050; margin: 0.15rem 0; }
    .paths { margin: 0.2rem 0 0.3rem 1rem; padding: 0; }
    .paths li { list-style: none; }
  `],
})
export class DomainScopePanelComponent implements OnInit {
  readonly svc = inject(DomainScopeService);
  readonly preview$ = new BehaviorSubject<ResolvedDomainScopePreview | null>(null);
  selectedId: string | null = null;

  ngOnInit(): void {
    this.svc.loadDomains();
    this.selectedId = this.svc.currentSelection();
    if (this.selectedId) this.refreshPreview(this.selectedId);
  }

  select(domain: DetectedDomain): void {
    this.selectedId = domain.domain_id;
    this.svc.selectDomain(domain.domain_id);
    this.refreshPreview(domain.domain_id);
  }

  clear(): void {
    this.selectedId = null;
    this.svc.selectDomain(null);
    this.preview$.next(null);
  }

  private refreshPreview(domainId: string): void {
    this.svc.previewScope([domainId]).subscribe({
      next: preview => this.preview$.next(preview),
      error: () => this.preview$.next(null),
    });
  }
}
