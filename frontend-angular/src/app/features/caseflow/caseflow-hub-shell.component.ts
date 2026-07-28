import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

@Component({
  standalone: true,
  selector: 'app-caseflow-hub-shell',
  imports: [RouterLink, RouterLinkActive, RouterOutlet],
  template: `
    <nav class="caseflow-hub-nav" aria-label="CaseFlow">
      <a routerLink="/caseflow" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">
        Szenarien
      </a>
      <a routerLink="/caseflow/studio" routerLinkActive="active">CaseFlow Studio</a>
    </nav>
    <router-outlet />
  `,
  styles: [`
    .caseflow-hub-nav {
      display: flex;
      gap: 1rem;
      padding: .75rem 1rem;
      border-bottom: 1px solid var(--border-color, #333);
    }
    .caseflow-hub-nav a { color: inherit; text-decoration: none; }
    .caseflow-hub-nav a.active { font-weight: 700; border-bottom: 2px solid currentColor; }
  `],
})
export class CaseFlowHubShellComponent {}
