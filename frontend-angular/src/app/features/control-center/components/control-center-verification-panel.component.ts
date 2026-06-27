import { Component, Input } from '@angular/core';

import { RouterLink } from '@angular/router';
import { CcVerificationSummary } from '../models/control-center.models';
import { StatusChipComponent } from './status-chip.component';

@Component({
  standalone: true,
  selector: 'app-control-center-verification-panel',
  imports: [RouterLink, StatusChipComponent],
  template: `
    @if (verification) {
      <section class="panel">
        <h4>Verification</h4>
        <app-status-chip [label]="verification.status" [tone]="tone(verification.status)" />
        <p>Tests: {{ verification.testCount }} · Passed: {{ verification.passedCount }} · Failed: {{ verification.failedCount }}</p>
        @if (verification.status!=='passed') {
          <p class="warn">Task-Abschluss sollte ohne erfolgreiche Verification blockiert bleiben.</p>
        }
        @if (reportArtifactId) {
          <a [routerLink]="['/control-center/artifacts']" class="link">Testreport-Artifact ansehen: {{ reportArtifactId }}</a>
        }
      </section>
    }
    `,
  styles: [`.panel{border:1px solid #1f2937;border-radius:10px;padding:10px;background:#0f172a}.warn{color:#fdba74}.link{color:#93c5fd}`]
})
export class ControlCenterVerificationPanelComponent {
  @Input() verification: CcVerificationSummary | null = null;
  @Input() reportArtifactId: string | null = null;
  tone(s: CcVerificationSummary['status']): 'neutral'|'ok'|'warn'|'danger'|'info' {
    if (s === 'passed') return 'ok';
    if (s === 'failed') return 'danger';
    if (s === 'running') return 'info';
    if (s === 'partial') return 'warn';
    return 'neutral';
  }
}
