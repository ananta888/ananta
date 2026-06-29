import { Component } from '@angular/core';
import { RouterModule } from '@angular/router';

@Component({
  standalone: true,
  selector: 'app-caseflow-shell',
  template: `
    <nav class="caseflow-nav">
      <a routerLink="" routerLinkActive="active" [routerLinkActiveOptions]="{exact:true}">Dashboard</a>
      <a routerLink="board" routerLinkActive="active">Board</a>
      <a routerLink="discovery" routerLinkActive="active">Discovery</a>
      <a routerLink="actions" routerLinkActive="active">Aktionen</a>
      <a routerLink="profiles" routerLinkActive="active">Suchprofile</a>
    </nav>
    <router-outlet />
  `,
  styles: [`
    .caseflow-nav { display: flex; gap: 1rem; padding: 0.5rem 1rem; border-bottom: 1px solid #333; }
    .caseflow-nav a { text-decoration: none; color: inherit; }
    .caseflow-nav a.active { font-weight: bold; border-bottom: 2px solid currentColor; }
  `],
  imports: [RouterModule],
})
export class CaseflowShellComponent {}
