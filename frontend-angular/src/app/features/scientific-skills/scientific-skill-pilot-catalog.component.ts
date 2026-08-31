import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

export interface ScientificSkillPilotCard {
  readonly entry_id: string;
  readonly skill_name: string;
  readonly upstream_pin: string;
  readonly allowed_mode: string;
  readonly context_budget_tokens: number;
  readonly allowed_tools: readonly string[];
  readonly network_profile: string;
  readonly approval_level: string;
  readonly approval_status: string;
  readonly source_reference: string;
}

@Component({
  selector: 'app-scientific-skill-pilot-catalog',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (featureEnabled && permissionGranted) {
      <section class="pilot" aria-labelledby="scientific-skill-pilot-title">
        <header>
          <p class="eyebrow">Geprüfter Pilot</p>
          <h2 id="scientific-skill-pilot-title">Scientific Skills</h2>
          <p>Nur freigegebene, unveränderlich gepinnte Dokumentationskontexte.</p>
        </header>
        <div class="cards">
          @for (card of cards; track card.entry_id) {
            <article class="card" [attr.data-skill-name]="card.skill_name">
              <h3>{{ card.skill_name }}</h3>
              <dl>
                <div><dt>Pin</dt><dd><code>{{ card.upstream_pin }}</code></dd></div>
                <div><dt>Fähigkeit</dt><dd>{{ card.allowed_mode }}</dd></div>
                <div><dt>Kontextbudget</dt><dd>{{ card.context_budget_tokens }} Tokens</dd></div>
                <div><dt>Netzwerk</dt><dd>{{ card.network_profile }}</dd></div>
                <div><dt>Freigabe</dt><dd>{{ card.approval_status }} / {{ card.approval_level }}</dd></div>
              </dl>
              <a [href]="card.source_reference" target="_blank" rel="noopener noreferrer">
                Gepinnte Quelle prüfen
              </a>
            </article>
          }
        </div>
      </section>
    }
  `,
  styles: [`
    .pilot { display: grid; gap: 1rem; }
    .eyebrow { font-size: .75rem; letter-spacing: .08em; text-transform: uppercase; }
    .cards { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(17rem, 1fr)); }
    .card { border: 1px solid currentColor; border-radius: .75rem; padding: 1rem; }
    dl, dl div { display: grid; gap: .25rem; }
    dl div { grid-template-columns: 7rem minmax(0, 1fr); }
    dt { font-weight: 600; }
    dd { margin: 0; overflow-wrap: anywhere; }
  `],
})
export class ScientificSkillPilotCatalogComponent {
  @Input({ required: true }) cards: readonly ScientificSkillPilotCard[] = [];
  @Input() featureEnabled = false;
  @Input() permissionGranted = false;
}
