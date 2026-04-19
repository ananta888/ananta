import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';

@Component({
  standalone: true,
  selector: 'app-help',
  imports: [RouterLink],
  template: `
    <section class="card help-page">
      <h2>Hilfe fuer den Einstieg</h2>
      <p class="muted">
        Starte mit einem Ziel, pruefe die Demo-Beispiele oder verfolge vorhandene Aufgaben und Ergebnisse.
      </p>
      <div class="grid cols-3 mt-md">
        <a class="card card-light help-action" routerLink="/dashboard">
          <strong>Ziel planen</strong>
          <span>Ein Satz reicht. Ananta erzeugt daraus planbare Aufgaben.</span>
        </a>
        <a class="card card-light help-action" routerLink="/board">
          <strong>Aufgaben verfolgen</strong>
          <span>Sieh, was offen, blockiert, in Arbeit oder abgeschlossen ist.</span>
        </a>
        <a class="card card-light help-action" routerLink="/artifacts">
          <strong>Ergebnisse ansehen</strong>
          <span>Oeffne erzeugte Artefakte und pruefe Resultate.</span>
        </a>
      </div>
    </section>
  `,
  styles: [`
    .help-page {
      max-width: 960px;
      margin: 0 auto;
    }
    .help-action {
      display: flex;
      flex-direction: column;
      gap: 8px;
      color: var(--fg);
      min-height: 112px;
    }
    .help-action span {
      color: var(--muted);
      line-height: 1.35;
    }
  `],
})
export class HelpComponent {}
