import { Component, OnInit, signal, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule, ActivatedRoute } from '@angular/router';
import { CaseFlowApiService } from '../caseflow-api.service';
import { CaseFlowCase } from '../caseflow.models';
import {
  JOB_STATUS_COLUMNS,
  JOB_STATUS_LABELS,
  JobStatus,
} from './job-application.models';

@Component({
  standalone: true,
  selector: 'app-application-board',
  imports: [CommonModule, RouterModule],
  template: `
    <div class="board">
      <div class="board-header">
        <h2>Bewerbungs-Kanban</h2>
        <div class="filters">
          <input type="text" placeholder="Suche..." (input)="setSearch($event)" />
        </div>
      </div>

      @if (loading()) {
        <p>Lade Bewerbungen...</p>
      } @else {
        <div class="columns">
          @for (col of columns; track col) {
            <div class="column">
              <div class="column-header">
                <span>{{ statusLabels[col] }}</span>
                <span class="badge">{{ cardsByStatus()[col]?.length ?? 0 }}</span>
              </div>
              <div class="cards">
                @for (c of cardsByStatus()[col]; track c.id) {
                  <div class="card" [routerLink]="['../', c.id]">
                    <div class="card-title">{{ c.title }}</div>
                    <div class="card-meta">
                      <span class="priority {{ c.priority }}">{{ c.priority }}</span>
                    </div>
                  </div>
                }
                @if (!cardsByStatus()[col]?.length) {
                  <div class="empty-state">Keine Bewerbungen</div>
                }
              </div>
            </div>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    .board { padding: 1rem; overflow-x: auto; }
    .board-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }
    .columns { display: flex; gap: 0.75rem; min-width: max-content; }
    .column { width: 200px; background: #1a1a1a; border-radius: 8px; padding: 0.5rem; }
    .column-header { display: flex; justify-content: space-between; padding: 0.5rem; font-weight: bold; font-size: 0.85rem; }
    .badge { background: #333; border-radius: 9999px; padding: 0 0.5rem; font-size: 0.75rem; }
    .card { background: #2a2a2a; border-radius: 6px; padding: 0.75rem; margin-bottom: 0.5rem; cursor: pointer; }
    .card:hover { background: #3a3a3a; }
    .card-title { font-size: 0.9rem; margin-bottom: 0.25rem; }
    .card-meta { font-size: 0.75rem; color: #aaa; }
    .priority.critical { color: #ef4444; }
    .priority.high { color: #f59e0b; }
    .priority.medium { color: #60a5fa; }
    .priority.low { color: #aaa; }
    .empty-state { color: #555; text-align: center; padding: 1rem; font-size: 0.85rem; }
    .filters input { background: #2a2a2a; border: 1px solid #444; color: #fff; padding: 0.4rem 0.8rem; border-radius: 4px; }
  `],
})
export class ApplicationBoardComponent implements OnInit {
  private readonly api = inject(CaseFlowApiService);
  private readonly route = inject(ActivatedRoute);

  columns = JOB_STATUS_COLUMNS;
  statusLabels = JOB_STATUS_LABELS;

  loading = signal(false);
  private allCases = signal<CaseFlowCase[]>([]);
  private search = signal('');

  cardsByStatus = computed(() => {
    const s = this.search().toLowerCase();
    const cases = this.allCases().filter(c =>
      !s || c.title.toLowerCase().includes(s)
    );
    const grouped: Record<string, CaseFlowCase[]> = {};
    for (const col of this.columns) grouped[col] = [];
    for (const c of cases) {
      if (grouped[c.status] !== undefined) grouped[c.status].push(c);
    }
    return grouped;
  });

  ngOnInit(): void {
    this.loading.set(true);
    const status = this.route.snapshot.queryParamMap.get('status') || undefined;
    this.api.listCases(status ? { case_type: 'job_application', status } : { case_type: 'job_application' }).subscribe({
      next: (res) => { this.allCases.set(res.items); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  setSearch(event: Event): void {
    this.search.set((event.target as HTMLInputElement).value);
  }
}
