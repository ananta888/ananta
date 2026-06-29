import { Component, Input, OnInit, signal, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { JobApplicationApiService } from './job-application-api.service';
import { JobFitScore, SubScore } from './job-application.models';

@Component({
  standalone: true,
  selector: 'app-fit-analysis-tab',
  imports: [CommonModule],
  template: `
    <div class="fit-analysis">
      @if (loading()) {
        <p>Lade Bewertung...</p>
      } @else if (!score()) {
        <p class="empty">Noch keine Bewertung vorhanden.</p>
      } @else {
        <div class="final-score">
          <span class="score-label">Gesamtbewertung</span>
          <span class="score-value">{{ (score()!.final_score ?? score()!.manual_override) | number:'1.0-1' }}</span>
          <span class="score-source">{{ score()!.source === 'manual' ? '(manuell)' : '(KI)' }}</span>
        </div>
        @if (score()!.manual_override !== undefined && score()!.manual_override !== null) {
          <div class="override-note">Manuelles Override: {{ score()!.manual_override | number:'1.0-1' }} — {{ score()!.manual_override_reason }}</div>
        }
        <div class="subscores">
          @for (sub of subScores(); track sub.label) {
            <div class="subscore">
              <div class="subscore-header">
                <span class="subscore-label">{{ sub.label }}</span>
                @if (sub.score !== undefined && sub.score !== null) {
                  <span class="subscore-value">{{ sub.score | number:'1.0-1' }}/10</span>
                } @else {
                  <span class="subscore-unknown">Unbekannt</span>
                }
              </div>
              <p class="subscore-explanation">{{ sub.explanation }}</p>
            </div>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    .fit-analysis { padding: 0.5rem 0; }
    .final-score { display: flex; align-items: baseline; gap: 0.5rem; margin-bottom: 1rem; }
    .score-label { color: #aaa; }
    .score-value { font-size: 2rem; font-weight: bold; color: #34d399; }
    .score-source { font-size: 0.8rem; color: #555; }
    .override-note { background: #1c1917; padding: 0.5rem; border-radius: 4px; font-size: 0.85rem; margin-bottom: 1rem; }
    .subscores { display: flex; flex-direction: column; gap: 0.5rem; }
    .subscore { background: #1e1e1e; border-radius: 6px; padding: 0.75rem; }
    .subscore-header { display: flex; justify-content: space-between; margin-bottom: 0.25rem; }
    .subscore-label { font-weight: bold; }
    .subscore-value { color: #60a5fa; }
    .subscore-unknown { color: #555; font-style: italic; }
    .subscore-explanation { font-size: 0.85rem; color: #aaa; margin: 0; }
    .empty { color: #555; }
  `],
})
export class FitAnalysisTabComponent implements OnInit {
  @Input() caseId = '';
  private readonly api = inject(JobApplicationApiService);
  loading = signal(true);
  score = signal<JobFitScore | null>(null);

  subScores() {
    const s = this.score();
    if (!s) return [];
    return [
      { label: 'Technisch', ...(s.technical_fit ?? { score: null, explanation: '' }) },
      { label: 'Domain', ...(s.domain_fit ?? { score: null, explanation: '' }) },
      { label: 'Seniorität', ...(s.seniority_fit ?? { score: null, explanation: '' }) },
      { label: 'Ort', ...(s.location_fit ?? { score: null, explanation: '' }) },
      { label: 'Remote', ...(s.remote_fit ?? { score: null, explanation: '' }) },
      { label: 'Gehalt', ...(s.salary_fit ?? { score: null, explanation: '' }) },
    ];
  }

  ngOnInit(): void {
    if (!this.caseId) return;
    this.api.getFitScore(this.caseId).subscribe({
      next: (s) => { this.score.set(s); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }
}
