import { Component, Input } from '@angular/core';

export type StatusTone = 'success' | 'warning' | 'error' | 'info' | 'active' | 'paused' | 'unknown';

@Component({
  standalone: true,
  selector: 'app-status-badge',
  template: `
    <span class="badge shared-status-badge" [class]="toneClass()" [attr.aria-label]="ariaLabel || label">
      @if (dot) {
        <span class="shared-status-dot" aria-hidden="true"></span>
      }
      {{ label }}
    </span>
  `,
})
export class StatusBadgeComponent {
  @Input() label = 'unknown';
  @Input() tone: StatusTone = 'unknown';
  @Input() dot = false;
  @Input() ariaLabel = '';

  toneClass(): string {
    return `status-${this.tone || 'unknown'}`;
  }
}
