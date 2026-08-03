import { ChangeDetectionStrategy, Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { ProjectContextService } from '../services/project-context.service';

@Component({
  selector: 'app-project-context-switcher',
  standalone: true,
  imports: [FormsModule, RouterLink],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="project-switcher" aria-label="Projektkontext">
      <label for="global-project-select">Projekt</label>
      <select
        id="global-project-select"
        [ngModel]="context.selectedProjectId()"
        (ngModelChange)="selectProject($event)"
        [disabled]="context.loading() || context.selectionBlocked()"
        [attr.title]="context.selectionBlocked() ? context.selectionBlockMessage() : null"
      >
        <option value="">Projekt auswaehlen</option>
        @for (project of context.projects(); track project.id) {
          <option [value]="project.id" [disabled]="project.status === 'archived'">
            {{ project.name }}{{ project.status === 'archived' ? ' (archiviert)' : '' }}
          </option>
        }
      </select>
      <a class="manage-projects" routerLink="/projects">Neues Projekt</a>
      @if (context.error()) {
        <span class="project-error" role="alert">{{ context.error() }}</span>
      }
    </section>
  `,
  styles: [`
    :host { display: block; min-width: 12rem; }
    .project-switcher { display: grid; grid-template-columns: auto minmax(8rem, 1fr) auto; align-items: center; gap: .35rem; }
    label { font-size: .7rem; color: var(--muted); }
    select { min-height: 1.9rem; max-width: 15rem; padding: .2rem .4rem; font: inherit; }
    .manage-projects { font-size: .72rem; white-space: nowrap; }
    .project-error { grid-column: 1 / -1; color: #b91c1c; font-size: .7rem; }
    @media (max-width: 760px) { .project-switcher { grid-template-columns: 1fr auto; } label { grid-column: 1 / -1; } }
  `],
})
export class ProjectContextSwitcherComponent implements OnInit {
  readonly context = inject(ProjectContextService);

  ngOnInit(): void {
    this.context.ensureLoaded().subscribe({ error: () => undefined });
  }

  selectProject(projectId: string): void {
    this.context.selectProject(projectId);
  }
}
