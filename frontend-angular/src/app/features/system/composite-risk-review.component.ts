import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import {
  COMPOSITE_RISK_REVIEW_WARNING,
  CompositeRiskReviewApiService,
  CompositeRiskReviewResult,
} from '../../services/composite-risk-review-api.service';

@Component({
  standalone: true,
  selector: 'app-composite-risk-review',
  imports: [FormsModule],
  template: `
    <section class="review-panel">
      <header>
        <p class="eyebrow">Expliziter Audit-Hinweis</p>
        <h1>Composite Risk Review</h1>
      </header>
      <aside class="warning" data-testid="composite-risk-warning">{{ warningText }}</aside>
      <p>Das Panel ist kein Safety-Status und trifft keine Freigabeentscheidung.</p>

      <label for="review-payload">Review-Payload (JSON)</label>
      <textarea id="review-payload" [(ngModel)]="payloadText" rows="14"></textarea>
      <button type="button" (click)="runReview()" [disabled]="loading">
        {{ loading ? 'Review läuft …' : 'Review explizit ausführen' }}
      </button>

      @if (error) {
        <p class="error" role="alert">{{ error }}</p>
      }
      @if (result) {
        <article class="result" data-testid="composite-risk-result">
          <h2>Hinweisstufe: {{ result.risk_level }}</h2>
          <p>{{ result.explanation }}</p>
          <p><strong>Automatische Empfehlung:</strong> {{ result.recommended_action }}</p>
          <ul>
            @for (indicator of result.indicators; track indicator.id) {
              <li><strong>{{ indicator.id }}</strong> — {{ indicator.description }}</li>
            }
          </ul>
          <p class="warning">{{ result.warning_text }}</p>
        </article>
      }
    </section>
  `,
  styles: [`
    .review-panel{max-width:900px;margin:24px auto;padding:24px;border:1px solid #334155;border-radius:12px;background:#0f172a;color:#e2e8f0}
    .eyebrow{color:#93c5fd;text-transform:uppercase;letter-spacing:.08em}.warning{padding:14px;border-left:4px solid #f59e0b;background:#422006;color:#fde68a}
    label{display:block;margin:18px 0 6px;font-weight:600}textarea{width:100%;box-sizing:border-box;background:#020617;color:#e2e8f0;border:1px solid #475569;border-radius:8px;padding:12px;font-family:monospace}
    button{margin-top:12px;padding:10px 16px;border:0;border-radius:8px;background:#2563eb;color:white}button:disabled{opacity:.55}.error{color:#fca5a5}.result{margin-top:20px;padding:16px;border:1px solid #475569;border-radius:8px}
  `],
})
export class CompositeRiskReviewComponent {
  private readonly api = inject(CompositeRiskReviewApiService);
  readonly warningText = COMPOSITE_RISK_REVIEW_WARNING;
  payloadText = JSON.stringify({ goal: '', tasks: [], artifacts_metadata: [], audit_events: [] }, null, 2);
  loading = false;
  error = '';
  result: CompositeRiskReviewResult | null = null;

  runReview(): void {
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(this.payloadText) as Record<string, unknown>;
    } catch {
      this.error = 'Der Review-Payload ist kein gültiges JSON-Objekt.';
      return;
    }
    if (!payload || Array.isArray(payload) || typeof payload !== 'object') {
      this.error = 'Der Review-Payload muss ein JSON-Objekt sein.';
      return;
    }
    this.loading = true;
    this.error = '';
    this.result = null;
    this.api.review(payload).subscribe({
      next: result => {
        this.result = result;
        this.loading = false;
      },
      error: () => {
        this.error = 'Composite Risk Review konnte nicht ausgeführt werden.';
        this.loading = false;
      },
    });
  }
}
