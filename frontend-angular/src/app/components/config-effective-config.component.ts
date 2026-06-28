import { ChangeDetectionStrategy, ChangeDetectorRef, Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { EffectiveConfig } from '../models/config-graph.model';
import { ConfigGraphService } from '../services/config-graph.service';

@Component({
  selector: 'app-config-effective-config',
  standalone: true,
  imports: [FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="sidebar-section-label">Effektiv auflösen</div>
    <div class="effective-form">
      <input [(ngModel)]="surface" placeholder="Surface (z.B. ai_snake_chat)" class="eff-input" />
      <input [(ngModel)]="taskKind" placeholder="Task-Kind (optional)" class="eff-input" />
      <input [(ngModel)]="path" placeholder="Pfad (optional)" class="eff-input" />
      <button class="button-outline full-w" (click)="resolve()">Auflösen →</button>
    </div>
    @if (result) {
      <div class="effective-panel">
        <div class="ep-header">
          <strong>Effektiv: {{ result.surface }}</strong>
          <button (click)="result=null; cdr.markForCheck()" class="close-btn">✕</button>
        </div>
        <div class="ep-grid">
          <div><div class="eff-label">Profil</div>{{ result.agent_profile?.['profile_id'] ?? '—' }}</div>
          <div><div class="eff-label">Template</div>{{ result.goal_template?.['template_id'] ?? '—' }}</div>
          <div>
            <div class="eff-label">Gesperrte Modi</div>
            @for (mode of result.effective_ai_modes_blocked; track mode) { <span class="tag warn">{{ mode }}</span> }
          </div>
          <div>
            <div class="eff-label">Erlaubte Modi</div>
            @for (mode of result.effective_ai_modes_allowed; track mode) { <span class="tag ok">{{ mode }}</span> }
          </div>
          @if (result.warnings.length) {
            <ul class="warn-list">@for (warning of result.warnings; track warning) { <li>{{ warning }}</li> }</ul>
          }
        </div>
      </div>
    }
  `,
  styleUrls: ['./config-graph-editor.component.scss'],
})
export class ConfigEffectiveConfigComponent {
  private readonly service = inject(ConfigGraphService);
  readonly cdr = inject(ChangeDetectorRef);
  surface = 'ai_snake_chat';
  taskKind = '';
  path = '';
  result: EffectiveConfig | null = null;

  resolve(): void {
    if (!this.surface.trim()) return;
    this.service.getEffectiveConfig({
      surface: this.surface.trim(),
      task_kind: this.taskKind.trim() || null,
      path: this.path.trim() || null,
    }).subscribe(result => {
      this.result = result;
      this.cdr.markForCheck();
    });
  }
}
