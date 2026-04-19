import { Component, Input } from '@angular/core';

import { PlatformTerm, decisionExplanation, userFacingTerm } from '../../../models/user-facing-language';
import { ExplanationNoticeComponent } from './explanation-notice.component';

@Component({
  standalone: true,
  selector: 'app-decision-explanation',
  imports: [ExplanationNoticeComponent],
  template: `
    <app-explanation-notice [title]="titleText()" [message]="messageText()"></app-explanation-notice>
  `,
})
export class DecisionExplanationComponent {
  @Input() kind: PlatformTerm = 'verification';
  @Input() title = '';
  @Input() message = '';

  titleText(): string {
    return this.title || `Warum ${userFacingTerm(this.kind).label}?`;
  }

  messageText(): string {
    return this.message || decisionExplanation(this.kind);
  }
}
