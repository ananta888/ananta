import { Component, Input } from '@angular/core';
import { StatusTone } from '../state/status-badge.component';

@Component({
  standalone: true,
  selector: 'app-metric-card',
  template: `
    <div class="card card-light shared-metric-card" [class]="toneClass()">
      <div class="muted font-sm">{{ label }}</div>
      <strong>{{ value }}</strong>
      @if (hint) {
        <div class="muted font-sm">{{ hint }}</div>
      }
    </div>
  `,
})
export class MetricCardComponent {
  @Input() label = '';
  @Input() value: string | number = '-';
  @Input() hint = '';
  @Input() tone: StatusTone | '' = '';

  toneClass(): string {
    return this.tone ? `metric-${this.tone}` : '';
  }
}
