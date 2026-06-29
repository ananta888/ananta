import { Component, OnInit, signal, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { CaseFlowApiService } from '../caseflow-api.service';
import { CaseAction } from '../caseflow.models';

@Component({
  standalone: true,
  selector: 'app-action-center',
  imports: [CommonModule, RouterModule],
  template: `
    <div class="actions">
      <h2>Aktionszentrum</h2>
      @if (loading()) {
        <p>Lade Aktionen...</p>
      } @else if (!actions().length) {
        <p class="empty">Keine offenen Aktionen.</p>
      } @else {
        @for (a of actions(); track a.id) {
          <div class="action-card" [class.overdue]="isOverdue(a)" [class.blocking]="a.blocking">
            <div class="action-header">
              <span class="action-title">{{ a.title }}</span>
              @if (a.blocking) { <span class="tag blocking-tag">Blocking</span> }
              @if (isOverdue(a)) { <span class="tag overdue-tag">Überfällig</span> }
            </div>
            <div class="action-meta">
              <span>{{ a.action_type }}</span>
              @if (a.due_at) { <span>Fällig: {{ a.due_at | date:'shortDate' }}</span> }
              <span class="priority-{{ a.priority }}">{{ a.priority }}</span>
              <a [routerLink]="['/caseflow/jobs', a.case_id]">→ Case</a>
            </div>
          </div>
        }
      }
    </div>
  `,
  styles: [`
    .actions { padding: 1rem; }
    .action-card { background: #1e1e1e; border-radius: 6px; padding: 0.75rem; margin-bottom: 0.5rem; }
    .action-card.overdue { border-left: 3px solid #ef4444; }
    .action-card.blocking { border-left: 3px solid #f59e0b; }
    .action-header { display: flex; gap: 0.5rem; align-items: center; margin-bottom: 0.25rem; }
    .action-title { font-weight: bold; }
    .action-meta { display: flex; gap: 1rem; font-size: 0.8rem; color: #aaa; }
    .action-meta a { color: #60a5fa; text-decoration: none; }
    .tag { border-radius: 4px; padding: 0.1rem 0.4rem; font-size: 0.75rem; }
    .blocking-tag { background: #78350f; color: #fbbf24; }
    .overdue-tag { background: #7f1d1d; color: #fca5a5; }
    .priority-critical { color: #ef4444; } .priority-high { color: #f59e0b; }
    .empty { color: #555; }
  `],
})
export class ActionCenterComponent implements OnInit {
  private readonly api = inject(CaseFlowApiService);
  loading = signal(true);
  actions = signal<CaseAction[]>([]);

  ngOnInit(): void {
    this.api.getOpenActions().subscribe({
      next: (a) => { this.actions.set(a); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  isOverdue(a: CaseAction): boolean {
    return !!a.due_at && new Date(a.due_at) < new Date();
  }
}
