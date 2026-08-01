import { ChangeDetectionStrategy, Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';

import { ProjectContextService } from '../../services/project-context.service';

@Component({
  selector: 'app-project-management',
  standalone: true,
  imports: [FormsModule],
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
              <li [attr.data-status]="project.status">
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
              </li>
            }
          </ul>
        }
      </section>

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
    li span, li p { display: block; margin: .2rem 0 0; color: var(--muted); }
    li[data-status="archived"] { opacity: .7; }
    .status { font-weight: 700; }
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

  private continueToReturnUrl(): void {
    const returnUrl = this.route.snapshot.queryParamMap.get('returnUrl')?.trim();
    if (returnUrl) {
      void this.router.navigateByUrl(this.context.urlWithProject(returnUrl));
    }
  }
}
