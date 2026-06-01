import { Component, Input } from '@angular/core';
import { NgFor } from '@angular/common';
import { CcToolCall } from '../models/control-center.models';
import { StatusChipComponent } from './status-chip.component';

@Component({
  standalone: true,
  selector: 'app-control-center-tool-timeline',
  imports: [NgFor, StatusChipComponent],
  template: `
    <h4>Tool Calls</h4>
    <div class="timeline">
      <div class="row" *ngFor="let t of items">
        <span>{{ t.toolName }}</span>
        <app-status-chip [label]="t.status" [tone]="tone(t.status)" />
      </div>
    </div>
  `,
  styles: [`.timeline{display:flex; flex-direction:column; gap:8px}.row{display:flex; justify-content:space-between; gap:8px; border:1px solid #1f2937; border-radius:8px; padding:6px 8px;}`]
})
export class ControlCenterToolTimelineComponent {
  @Input() items: CcToolCall[] = [];
  tone(status: CcToolCall['status']): 'neutral'|'ok'|'warn'|'danger'|'info' {
    if (status === 'completed' || status === 'allowed') return 'ok';
    if (status === 'denied' || status === 'failed') return 'danger';
    if (status === 'running') return 'info';
    return 'neutral';
  }
}
