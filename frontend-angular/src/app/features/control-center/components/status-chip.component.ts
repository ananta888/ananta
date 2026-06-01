import { Component, Input } from '@angular/core';
import { NgClass } from '@angular/common';

@Component({
  standalone: true,
  selector: 'app-status-chip',
  imports: [NgClass],
  template: `<span class="chip" [ngClass]="tone">{{ label }}</span>`,
  styles: [`
    .chip { border-radius: 999px; padding: 2px 10px; font-size: 12px; border: 1px solid transparent; white-space: nowrap; }
    .neutral { background:#1f2937; color:#d1d5db; border-color:#374151; }
    .ok { background:#052e16; color:#86efac; border-color:#166534; }
    .warn { background:#3f1d0a; color:#fdba74; border-color:#9a3412; }
    .danger { background:#450a0a; color:#fca5a5; border-color:#991b1b; }
    .info { background:#172554; color:#93c5fd; border-color:#1d4ed8; }
  `]
})
export class StatusChipComponent {
  @Input() label = '';
  @Input() tone: 'neutral' | 'ok' | 'warn' | 'danger' | 'info' = 'neutral';
}
