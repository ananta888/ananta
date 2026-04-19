import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { USER_FACING_TERMS } from '../models/user-facing-language';

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
      <section class="mt-lg" aria-label="Begriffe">
        <h3>Wichtige Begriffe</h3>
        <div class="grid cols-3 mt-sm">
          @for (entry of glossaryEntries; track entry.term) {
            <div class="card card-light help-term">
              <strong>{{ entry.label }}</strong>
              <span class="muted">{{ entry.technicalLabel }}</span>
              <p class="no-margin">{{ entry.hint }}</p>
            </div>
          }
        </div>
      </section>
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
    .help-term {
      min-height: 128px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
  `],
})
export class HelpComponent {
  glossaryEntries = [
    USER_FACING_TERMS.artifact,
    USER_FACING_TERMS.blueprint,
    USER_FACING_TERMS.verification,
    USER_FACING_TERMS['exposure-policy'],
    USER_FACING_TERMS.federation,
    USER_FACING_TERMS.routing,
  ];
}
