import { Component, Input } from '@angular/core';

import { TrainingMetric } from '../model-training.models';

@Component({
  selector: 'app-training-metrics-chart',
  standalone: true,
  template: `
    <figure class="metrics-chart">
      <svg viewBox="0 0 800 180" preserveAspectRatio="none" role="img" [attr.aria-label]="ariaLabel()">
        <title>{{ ariaLabel() }}</title>
        <line x1="0" y1="179" x2="800" y2="179" stroke="currentColor" opacity="0.25" />
        @if (trainPoints()) { <polyline [attr.points]="trainPoints()" fill="none" stroke="var(--accent)" stroke-width="3" vector-effect="non-scaling-stroke" /> }
        @if (evalPoints()) { <polyline [attr.points]="evalPoints()" fill="none" stroke="var(--warning)" stroke-width="3" vector-effect="non-scaling-stroke" /> }
      </svg>
      <figcaption><span class="train">Train Loss</span><span class="eval">Eval Loss</span></figcaption>
    </figure>
  `,
  styles: [`
    .metrics-chart { margin:0; display:grid; gap:5px; }
    svg { width:100%; height:180px; border:1px solid var(--border); border-radius:8px; background:color-mix(in srgb,var(--card-bg) 96%,var(--fg)); }
    figcaption { display:flex; gap:16px; color:var(--muted); font-size:12px; }
    figcaption span::before { content:''; display:inline-block; width:14px; height:3px; margin-right:5px; vertical-align:middle; }
    .train::before { background:var(--accent); } .eval::before { background:var(--warning); }
  `],
})
export class TrainingMetricsChartComponent {
  @Input() metrics: TrainingMetric[] = [];

  trainPoints(): string { return this.points('train_loss'); }
  evalPoints(): string { return this.points('eval_loss'); }

  ariaLabel(): string {
    const train = this.metrics.filter(metric => Number.isFinite(metric.train_loss)).length;
    const evaluation = this.metrics.filter(metric => Number.isFinite(metric.eval_loss)).length;
    return `Verlauf der Trainingsmetriken mit ${train} Train-Loss- und ${evaluation} Eval-Loss-Werten`;
  }

  private points(key: 'train_loss' | 'eval_loss'): string {
    const values = this.metrics
      .filter(metric => Number.isFinite(metric[key]))
      .map(metric => ({ step: Number(metric.step), value: Number(metric[key]) }));
    if (!values.length) return '';
    const maxStep = Math.max(...values.map(item => item.step), 1);
    const maxValue = Math.max(...values.map(item => item.value), 0.000001);
    return values.map(item => `${(item.step / maxStep) * 800},${179 - ((item.value / maxValue) * 170)}`).join(' ');
  }
}
