import { Component, Input } from '@angular/core';
import { MetricCardComponent } from './metric-card.component';
import { StatusTone } from '../state/status-badge.component';

export interface SummaryMetric {
  label: string;
  value: string | number;
  hint?: string;
  tone?: StatusTone | '';
}

@Component({
  standalone: true,
  selector: 'app-summary-panel',
  imports: [MetricCardComponent],
  template: `
    <section class="card card-light shared-summary-panel" [attr.aria-label]="ariaLabel || title">
      <div>
        @if (eyebrow) {
          <div class="muted font-sm mb-xs">{{ eyebrow }}</div>
        }
        @if (title) {
          <h4 class="no-margin">{{ title }}</h4>
        }
        @if (summary) {
          <p class="muted mt-sm no-margin">{{ summary }}</p>
        }
      </div>
      @if (metrics.length) {
        <div class="grid shared-summary-metrics" [class.cols-2]="columns === 2" [class.cols-3]="columns === 3">
          @for (metric of metrics; track metric.label) {
            <app-metric-card [label]="metric.label" [value]="metric.value" [hint]="metric.hint || ''" [tone]="metric.tone || ''"></app-metric-card>
          }
        </div>
      }
      <ng-content></ng-content>
    </section>
  `,
})
export class SummaryPanelComponent {
  @Input() title = '';
  @Input() summary = '';
  @Input() eyebrow = '';
  @Input() ariaLabel = '';
  @Input() metrics: SummaryMetric[] = [];
  @Input() columns: 2 | 3 = 3;
}
