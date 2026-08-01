import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { SourceControlCenterFacade } from '../features/sources/source-control-center.facade';

@Component({
  standalone: true,
  selector: 'app-sources',
  imports: [RouterLink, RouterLinkActive, RouterOutlet],
  providers: [SourceControlCenterFacade],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="source-shell">
      <header class="shell-head">
        <div>
          <p class="eyebrow">Control Plane</p>
          <h1>Source Control Center</h1>
        </div>
        <nav aria-label="Source Control Center">
          <a routerLink="/sources" queryParamsHandling="preserve" routerLinkActive="active"
            [routerLinkActiveOptions]="{ exact: true }">Übersicht</a>
          <a routerLink="/sources/add" queryParamsHandling="preserve" routerLinkActive="active">Hinzufügen</a>
        </nav>
      </header>
      <router-outlet />
    </section>
  `,
  styles: [`
    :host { display: block; }
    .source-shell { max-width: 1240px; margin: 0 auto; padding: 18px; }
    .shell-head { display: flex; align-items: end; justify-content: space-between; gap: 16px; margin-bottom: 24px; padding-bottom: 14px; border-bottom: 1px solid var(--border); }
    h1 { margin: 2px 0 0; font-size: clamp(24px, 4vw, 38px); }
    .eyebrow { margin: 0; color: var(--accent); font-size: 11px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; }
    nav { display: flex; gap: 6px; }
    nav a { padding: 7px 11px; border-radius: 7px; color: var(--muted); text-decoration: none; }
    nav a.active { background: var(--accent); color: #fff; }
    @media (max-width: 640px) { .shell-head { align-items: stretch; flex-direction: column; } }
  `],
})
export class SourcesComponent {}
