import { Component, Input } from '@angular/core';

export type NoticeTone = 'info' | 'success' | 'warning' | 'error' | 'technical';

@Component({
  standalone: true,
  selector: 'app-explanation-notice',
  template: `
    <aside class="shared-notice" [class]="toneClass()" [attr.aria-label]="ariaLabel || title">
      @if (title) {
        <strong>{{ title }}</strong>
      }
      @if (message) {
        <p class="muted no-margin mt-sm">{{ message }}</p>
      }
      <ng-content></ng-content>
    </aside>
  `,
})
export class ExplanationNoticeComponent {
  @Input() title = '';
  @Input() message = '';
  @Input() ariaLabel = '';
  @Input() tone: NoticeTone = 'info';

  toneClass(): string {
    return `notice-${this.tone}`;
  }
}
