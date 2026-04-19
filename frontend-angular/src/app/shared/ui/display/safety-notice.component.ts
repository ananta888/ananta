import { Component, Input } from '@angular/core';
import { ExplanationNoticeComponent, NoticeTone } from './explanation-notice.component';

@Component({
  standalone: true,
  selector: 'app-safety-notice',
  imports: [ExplanationNoticeComponent],
  template: `
    <app-explanation-notice
      [title]="title"
      [message]="message"
      [tone]="tone"
      [ariaLabel]="ariaLabel || title"
    >
      <ng-content></ng-content>
    </app-explanation-notice>
  `,
})
export class SafetyNoticeComponent {
  @Input() title = 'Sicherheitsgrenze';
  @Input() message = '';
  @Input() ariaLabel = '';
  @Input() tone: NoticeTone = 'warning';
}
