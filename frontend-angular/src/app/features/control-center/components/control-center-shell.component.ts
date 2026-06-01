import { AsyncPipe, NgFor } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { RouterLink, RouterOutlet } from '@angular/router';
import { ControlCenterStateFacade } from '../services/control-center-state.facade';

@Component({
  standalone: true,
  selector: 'app-control-center-shell',
  imports: [RouterOutlet, RouterLink, AsyncPipe, NgFor],
  template: `
    <section class="cc-shell">
      <aside class="cc-left">
        <h3>Control Center</h3>
        <label class="muted">Projekt</label>
        <div class="project-list">
          <button
            type="button"
            *ngFor="let p of (state.projects$ | async) || []"
            class="project-btn"
            [class.project-btn--active]="(state.selectedProjectId$ | async) === p.id"
            (click)="state.selectProject(p.id)"
          >
            {{ p.name || p.id }}
          </button>
        </div>
        <nav>
          <a routerLink="dashboard">Dashboard</a>
          <a routerLink="tasks">Tasks</a>
          <a routerLink="sessions">Sessions</a>
          <a routerLink="artifacts">Artifacts</a>
          <a routerLink="workers">Workers</a>
          <a routerLink="policies">Policies</a>
          <a routerLink="codecompass">CodeCompass</a>
        </nav>
      </aside>
      <main class="cc-center"><router-outlet /></main>
      <aside class="cc-right">
        <h4>Agent / Policy Inspector</h4>
        <p class="muted">Worker, Modell, Runtime, erlaubte/verweigerte Tools und Pfade.</p>
      </aside>
    </section>
  `,
  styles: [`
    .cc-shell { display:grid; grid-template-columns: 220px 1fr 280px; gap:12px; min-height: calc(100vh - 120px); }
    .cc-left,.cc-center,.cc-right{ border:1px solid #1f2937; border-radius:12px; background:#0b1220; color:#e5e7eb; padding:12px; }
    .project-list{display:flex;flex-direction:column;gap:6px;margin:8px 0 12px;}
    .project-btn{background:#111827;color:#e5e7eb;border:1px solid #334155;border-radius:8px;padding:6px 8px;text-align:left;cursor:pointer}
    .project-btn--active{border-color:#60a5fa;background:#0f172a}
    nav{ display:flex; flex-direction:column; gap:8px; }
    a{ color:#93c5fd; text-decoration:none; }
    .muted{ color:#94a3b8; font-size:12px; }
    @media (max-width: 1100px){ .cc-shell{ grid-template-columns: 200px 1fr; } .cc-right{ grid-column: 1 / -1; } }
    @media (max-width: 800px){ .cc-shell{ grid-template-columns: 1fr; } }
  `]
})
export class ControlCenterShellComponent implements OnInit {
  readonly state = inject(ControlCenterStateFacade);

  ngOnInit(): void {
    this.state.loadProjects();
  }
}
