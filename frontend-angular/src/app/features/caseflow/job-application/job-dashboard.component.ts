import { Component, OnInit, signal, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { JobApplicationApiService } from './job-application-api.service';
import { JOB_STATUS_LABELS, JobStatus } from './job-application.models';

interface DashboardStat {
  label: string;
  count: number;
  status: JobStatus | null;
}

@Component({
  standalone: true,
  selector: 'app-job-dashboard',
  imports: [CommonModule, RouterModule],
  template: `
    <div class="dashboard">
      <h2>Bewerbungs-Dashboard</h2>

      <div class="stats-grid">
        @for (stat of stats(); track stat.label) {
          <div class="stat-card" [class.warning]="stat.count > 5 && stat.status === 'preparing'"
               [routerLink]="['board']" [queryParams]="stat.status ? { status: stat.status } : {}">
            <span class="stat-count">{{ stat.count }}</span>
            <span class="stat-label">{{ stat.label }}</span>
          </div>
        }
      </div>

      @if (loading()) {
        <p>Lade Bewerbungen...</p>
      }
      @if (error()) {
        <p class="error">{{ error() }}</p>
      }

      <div class="quick-links">
        <a routerLink="board">Kanban-Board</a>
        <a routerLink="discovery">Discovery Inbox</a>
        <a routerLink="actions">Aktionszentrum</a>
      </div>
    </div>
  `,
  styles: [`
    .dashboard { padding: 1rem; }
    .stats-grid { display: flex; flex-wrap: wrap; gap: 1rem; margin: 1rem 0; }
    .stat-card {
      background: #1e1e1e; border: 1px solid #333; border-radius: 8px;
      padding: 1rem 1.5rem; cursor: pointer; min-width: 120px; text-align: center;
    }
    .stat-card:hover { border-color: #666; }
    .stat-card.warning { border-color: #f59e0b; }
    .stat-count { display: block; font-size: 2rem; font-weight: bold; }
    .stat-label { display: block; font-size: 0.85rem; color: #aaa; }
    .quick-links { display: flex; gap: 1rem; margin-top: 1rem; }
    .quick-links a { color: #60a5fa; text-decoration: none; }
    .error { color: #ef4444; }
  `],
})
export class JobDashboardComponent implements OnInit {
  private readonly api = inject(JobApplicationApiService);

  loading = signal(false);
  error = signal<string | null>(null);
  private cases = signal<any[]>([]);

  stats = computed<DashboardStat[]>(() => {
    const all = this.cases();
    const byStatus = (status: JobStatus) =>
      all.filter((c: any) => (c.status || c.case?.status) === status).length;

    return [
      { label: 'Gesamt', count: all.length, status: null },
      { label: JOB_STATUS_LABELS.found, count: byStatus('found'), status: 'found' },
      { label: JOB_STATUS_LABELS.interesting, count: byStatus('interesting'), status: 'interesting' },
      { label: JOB_STATUS_LABELS.preparing, count: byStatus('preparing'), status: 'preparing' },
      { label: JOB_STATUS_LABELS.applied, count: byStatus('applied'), status: 'applied' },
      { label: JOB_STATUS_LABELS.waiting_response, count: byStatus('waiting_response'), status: 'waiting_response' },
      { label: JOB_STATUS_LABELS.interview, count: byStatus('interview'), status: 'interview' },
      { label: JOB_STATUS_LABELS.offer, count: byStatus('offer'), status: 'offer' },
      { label: JOB_STATUS_LABELS.rejected, count: byStatus('rejected'), status: 'rejected' },
    ];
  });

  ngOnInit(): void {
    this.loading.set(true);
    this.api.listJobApplications().subscribe({
      next: (items) => {
        this.cases.set(items);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set('Fehler beim Laden der Bewerbungen');
        this.loading.set(false);
      },
    });
  }
}
