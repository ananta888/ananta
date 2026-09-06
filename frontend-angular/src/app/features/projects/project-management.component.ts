import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';

import { ProjectContextService } from '../../services/project-context.service';
import { MeetPanelComponent } from '../meet/meet-panel.component';

@Component({
  selector: 'app-project-management',
  standalone: true,
  imports: [FormsModule, MeetPanelComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <main class="projects" aria-labelledby="projects-title">
      <header>
        <p class="eyebrow">Globaler Kontext</p>
        <h1 id="projects-title">Projekte</h1>
        <p>Projekt anlegen oder den aktiven Kontext fuer alle Arbeitsflaechen waehlen.</p>
      </header>

      <section class="create-card" aria-labelledby="create-project-title">
        <h2 id="create-project-title">Neues Projekt</h2>
        <label for="project-name">Name</label>
        <input
          id="project-name"
          [ngModel]="name()"
          (ngModelChange)="name.set($event)"
          maxlength="160"
          required
        />
        <label for="project-description">Beschreibung (optional)</label>
        <textarea
          id="project-description"
          [ngModel]="description()"
          (ngModelChange)="description.set($event)"
          maxlength="2000"
          rows="4"
        ></textarea>
        <button type="button" (click)="createProject()" [disabled]="!name().trim() || context.loading()">
          {{ context.loading() ? 'Projekt wird erstellt...' : 'Projekt erstellen' }}
        </button>
      </section>

      <section aria-labelledby="available-projects-title">
        <h2 id="available-projects-title">Verfuegbare Projekte</h2>
        @if (context.loading() && context.projects().length === 0) {
          <p role="status">Projekte werden geladen.</p>
        } @else if (context.projects().length === 0) {
          <p role="status">Noch keine Projekte vorhanden.</p>
        } @else {
          <ul>
            @for (project of context.projects(); track project.id) {
              <li [attr.data-status]="project.status" [class.selected]="context.selectedProjectId() === project.id">
                <div>
                  <strong>{{ project.name }}</strong>
                  <span>{{ project.id }}</span>
                  @if (project.description) { <p>{{ project.description }}</p> }
                </div>
                <span class="status">{{ project.status === 'active' ? 'Aktiv' : 'Archiviert' }}</span>
                <button
                  type="button"
                  (click)="selectProject(project.id)"
                  [disabled]="project.status === 'archived' || context.selectedProjectId() === project.id"
                >
                  {{ context.selectedProjectId() === project.id ? 'Ausgewaehlt' : 'Auswaehlen' }}
                </button>
                @if (context.selectedProjectId() === project.id) {
                  <button
                    type="button"
                    class="source-cta"
                    (click)="openSourceJourney(project.id)"
                  >
                    Git oder Ordner hinzufügen
                  </button>
                }
              </li>
            }
          </ul>
        }
      </section>

      @if (context.selectedProjectId(); as projectId) {
        <app-meet-panel [projectId]="projectId" />
      }
      @if (context.error()) {
        <p class="error" role="alert">{{ context.error() }}</p>
      }
    </main>
  `,
  styles: [`
    :host { display: block; }
    .projects { max-width: 68rem; margin: 0 auto; padding: 2rem; display: grid; gap: 1.5rem; }
    .eyebrow { margin: 0; text-transform: uppercase; letter-spacing: .14em; font-weight: 700; color: var(--accent); }
    h1, h2 { margin: .2rem 0; }
    .create-card { display: grid; gap: .55rem; padding: 1.2rem; border: 1px solid var(--border); border-radius: .75rem; background: var(--card-bg); }
    input, textarea, button { padding: .65rem; font: inherit; }
    ul { display: grid; gap: .6rem; margin: 0; padding: 0; list-style: none; }
    li { display: grid; grid-template-columns: 1fr auto auto; gap: .8rem; align-items: center; padding: 1rem; border: 1px solid var(--border); border-radius: .7rem; }
    li.selected { border-color: #176b5b; box-shadow: 0 8px 24px rgb(15 91 78 / 12%); }
    li span, li p { display: block; margin: .2rem 0 0; color: var(--muted); }
    li[data-status="archived"] { opacity: .7; }
    .status { font-weight: 700; }
    .source-cta { grid-column: 1 / -1; padding: .85rem 1rem; border: 1px solid #0e5e50; border-radius: .55rem; color: #fff; background: linear-gradient(105deg, #0e5e50, #167565); font-weight: 750; }
    .error { color: #b91c1c; }
    @media (max-width: 42rem) { .projects { padding: 1rem; } li { grid-template-columns: 1fr; } }
  `],
})
export class ProjectManagementComponent implements OnInit {
  readonly context = inject(ProjectContextService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  readonly name = signal('');
  readonly description = signal('');

  ngOnInit(): void {
    this.context.ensureLoaded().subscribe({ error: () => undefined });
  }

  createProject(): void {
    this.context.createProject({
      name: this.name(),
      description: this.description(),
    }).subscribe({
      next: () => {
        this.name.set('');
        this.description.set('');
        this.continueToReturnUrl();
      },
      error: () => undefined,
    });
  }

  selectProject(projectId: string): void {
    if (this.context.selectProject(projectId)) {
      this.continueToReturnUrl();
    }
  }

  openSourceJourney(projectId: string): void {
    const normalizedId = String(projectId || '').trim();
    if (
      !normalizedId
      || (this.context.selectedProjectId() !== normalizedId
        && !this.context.selectProject(normalizedId, false))
    ) {
      return;
    }
    void this.router.navigateByUrl(
      this.context.urlWithProject('/sources/journey', normalizedId),
    );
  }

  private continueToReturnUrl(): void {
    const returnUrl = this.route.snapshot.queryParamMap.get('returnUrl')?.trim();
    if (returnUrl) {
      void this.router.navigateByUrl(this.context.urlWithProject(returnUrl));
    }
  }
}
