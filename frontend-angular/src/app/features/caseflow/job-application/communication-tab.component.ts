import { Component, Input, OnInit, signal, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { CaseFlowApiService } from '../caseflow-api.service';
import { CaseArtifact } from '../caseflow.models';

@Component({
  standalone: true,
  selector: 'app-communication-tab',
  imports: [CommonModule],
  template: `
    <div class="communication">
      <p class="hint">E-Mail-Entwürfe und Kommunikationsdokumente</p>
      @if (loading()) {
        <p>Lade Kommunikation...</p>
      } @else if (!emailArtifacts().length) {
        <p class="empty">Noch keine E-Mail-Entwürfe vorhanden.</p>
      } @else {
        @for (a of emailArtifacts(); track a.id) {
          <div class="email-card">
            <div class="email-header">
              <span class="email-type">{{ a.artifact_type }}</span>
              <span class="email-status {{ a.status }}">{{ a.status }}</span>
            </div>
            <div class="email-title">{{ a.title }}</div>
            @if (a.content_text) {
              <pre class="email-preview">{{ a.content_text | slice:0:200 }}{{ a.content_text.length > 200 ? '...' : '' }}</pre>
            }
            <div class="email-meta">
              <span>v{{ a.version }}</span>
              <span>{{ a.source }}</span>
              @if (a.trace_id) { <span class="trace">Trace: {{ a.trace_id.substring(0, 8) }}</span> }
            </div>
          </div>
        }
      }
    </div>
  `,
  styles: [`
    .communication { padding: 0.5rem 0; }
    .hint { color: #666; font-size: 0.85rem; margin-bottom: 1rem; }
    .email-card { background: #1e1e1e; border-radius: 6px; padding: 0.75rem; margin-bottom: 0.75rem; }
    .email-header { display: flex; gap: 0.5rem; align-items: center; margin-bottom: 0.25rem; }
    .email-type { color: #60a5fa; font-size: 0.8rem; }
    .email-status { border-radius: 4px; padding: 0.1rem 0.4rem; font-size: 0.75rem; background: #333; }
    .email-status.approved { background: #064e3b; color: #34d399; }
    .email-title { font-weight: bold; margin-bottom: 0.5rem; }
    .email-preview { background: #111; padding: 0.5rem; border-radius: 4px; font-size: 0.8rem; white-space: pre-wrap; }
    .email-meta { display: flex; gap: 1rem; font-size: 0.75rem; color: #555; margin-top: 0.5rem; }
    .trace { color: #a78bfa; font-family: monospace; }
    .empty { color: #555; }
  `],
})
export class CommunicationTabComponent implements OnInit {
  @Input() caseId = '';
  private readonly api = inject(CaseFlowApiService);
  loading = signal(true);
  private allArtifacts = signal<CaseArtifact[]>([]);

  emailArtifacts = computed(() =>
    this.allArtifacts().filter(a =>
      ['email_draft', 'cover_letter', 'followup_email'].includes(a.artifact_type)
    )
  );

  ngOnInit(): void {
    if (!this.caseId) return;
    this.api.getArtifacts(this.caseId).subscribe({
      next: (a) => { this.allArtifacts.set(a); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }
}
