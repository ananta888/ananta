import { Component, Input, OnInit, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { CaseFlowApiService } from '../caseflow-api.service';
import { CaseArtifact } from '../caseflow.models';

@Component({
  standalone: true,
  selector: 'app-artifacts-tab',
  imports: [CommonModule],
  template: `
    <div class="artifacts">
      @if (loading()) {
        <p>Lade Dokumente...</p>
      } @else if (!artifacts().length) {
        <p class="empty">Keine Dokumente vorhanden.</p>
      } @else {
        @for (a of artifacts(); track a.id) {
          <div class="artifact-row">
            <div class="artifact-info">
              <span class="artifact-type">{{ a.artifact_type }}</span>
              <span class="artifact-title">{{ a.title }}</span>
              <span class="artifact-status {{ a.status }}">{{ a.status }}</span>
              @if (a.is_sensitive) { <span class="sensitive-tag">Sensibel</span> }
            </div>
            <div class="artifact-meta">
              <span>v{{ a.version }}</span>
              <span>{{ a.source }}</span>
              @if (a.trace_id) { <span class="trace-link">Trace: {{ a.trace_id.substring(0, 8) }}...</span> }
              <span>{{ a.created_at | date:'shortDate' }}</span>
            </div>
          </div>
        }
      }
    </div>
  `,
  styles: [`
    .artifacts { padding: 0.5rem 0; }
    .artifact-row { background: #1e1e1e; border-radius: 6px; padding: 0.75rem; margin-bottom: 0.5rem; }
    .artifact-info { display: flex; gap: 0.75rem; align-items: center; margin-bottom: 0.25rem; }
    .artifact-type { color: #60a5fa; font-size: 0.8rem; }
    .artifact-title { font-weight: bold; }
    .artifact-status { border-radius: 4px; padding: 0.1rem 0.4rem; font-size: 0.75rem; background: #333; }
    .artifact-status.approved { background: #064e3b; color: #34d399; }
    .artifact-status.generated { background: #1e3a8a; color: #93c5fd; }
    .artifact-status.draft { background: #374151; }
    .sensitive-tag { background: #7f1d1d; color: #fca5a5; border-radius: 4px; padding: 0.1rem 0.4rem; font-size: 0.75rem; }
    .artifact-meta { display: flex; gap: 1rem; font-size: 0.8rem; color: #aaa; }
    .trace-link { color: #a78bfa; font-family: monospace; }
    .empty { color: #555; }
  `],
})
export class ArtifactsTabComponent implements OnInit {
  @Input() caseId = '';
  private readonly api = inject(CaseFlowApiService);
  loading = signal(true);
  artifacts = signal<CaseArtifact[]>([]);

  ngOnInit(): void {
    if (!this.caseId) return;
    this.api.getArtifacts(this.caseId).subscribe({
      next: (a) => { this.artifacts.set(a); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }
}
