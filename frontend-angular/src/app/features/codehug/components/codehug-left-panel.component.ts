import { Component, ChangeDetectionStrategy } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

/**
 * Linke Spalte: Projekt-/Filter-Bereich der CodeHug-Spezialansicht.
 * Wird per <ng-content select="[chLeft]"> in die Shell projiziert.
 */
@Component({
  selector: 'ch-left-panel',
  standalone: true,
  imports: [RouterLink, RouterLinkActive],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <nav aria-label="Projekt-Navigation">
      <h4 class="ch-panel-title">Projekt</h4>
      <p class="ch-panel-empty">Projektauswahl folgt in CH-002-001.</p>

      <h4 class="ch-panel-title">Filter</h4>
      <p class="ch-panel-empty">Filter (Domain, Sprache, …) folgen in CH-007.</p>

      <h4 class="ch-panel-title">Navigation</h4>
      <ul class="ch-panel-list">
        <li><a routerLink="." routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">Dashboard</a></li>
        <li><a routerLink="context" routerLinkActive="active">Kontext-Builder</a></li>
        <li><a routerLink="agents" routerLinkActive="active">Agenten</a></li>
        <li><a routerLink="internals" routerLinkActive="active">Show Internals</a></li>
      </ul>
    </nav>
  `,
  styles: [`
    :host { display: block; font-size: 12px; }
    .ch-panel-title {
      margin: 12px 0 6px;
      font-size: 11px;
      letter-spacing: 0.6px;
      text-transform: uppercase;
      color: var(--muted);
    }
    .ch-panel-empty { color: var(--muted); margin: 0 0 4px; font-size: 12px; }
    .ch-panel-list { list-style: none; padding: 0; margin: 0; display: grid; gap: 4px; }
    .ch-panel-list a {
      display: block;
      padding: 6px 8px;
      border-radius: 6px;
      text-decoration: none;
      color: var(--fg);
    }
    .ch-panel-list a:hover { background: color-mix(in srgb, var(--accent) 12%, transparent); }
    .ch-panel-list a.active { background: color-mix(in srgb, var(--accent) 22%, transparent); font-weight: 600; }
  `]
})
export class CodeHugLeftPanelComponent {}