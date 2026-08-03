import { CommonModule } from '@angular/common';
import { Component, computed, effect, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { OrganizationBlueprintSummary } from '../models/organization-topology.models';
import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';

@Component({
  selector: 'app-organization-setup',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <section class="setup" aria-labelledby="organization-setup-heading">
      <header>
        <p class="eyebrow">Definition &amp; Dry-run</p>
        <h2 id="organization-setup-heading">Organisation einrichten</h2>
        <p>Standardmäßig wird die mittlere Acht-Team-Komposition verwendet. Alternativ ist eine explizit freigegebene Custom-N-Komposition bis zum servergelieferten Limit möglich.</p>
      </header>

      <div class="steps" aria-label="Einrichtungsschritte">
        <span [class.active]="step === 1">1 · Komposition</span>
        <span [class.active]="step === 2">2 · Dry-run</span>
        <span [class.active]="step === 3">3 · Bestätigung</span>
      </div>

      @if (step === 1) {
        <form class="form-grid" (ngSubmit)="compile()">
          <label>
            Name
            <input name="organization-title" [(ngModel)]="title" maxlength="120" required />
          </label>
          <label>
            Kompositionsmodus
            <select name="composition-mode" [(ngModel)]="compositionMode" (ngModelChange)="changeCompositionMode()">
              <option value="standard">Standard · 5 bis 10 Teams</option>
              <option value="custom">Custom N · 2 bis Serverlimit</option>
            </select>
          </label>

          @if (compositionMode === 'standard') {
            <label>
              Standardgröße
              <select name="team-count" [(ngModel)]="teamCount" (ngModelChange)="selectForCount()">
                @for (count of supportedCounts(); track count) {
                  <option [ngValue]="count">{{ count }} Teams{{ count === 8 ? ' · empfohlen' : '' }}</option>
                }
              </select>
            </label>
            <label class="wide">
              Blueprint
              <select name="blueprint" [(ngModel)]="blueprintKey" required>
                @for (blueprint of productionBlueprints(); track blueprint.key + ':' + blueprint.version) {
                  <option [value]="blueprint.key">{{ blueprint.title }} · v{{ blueprint.version }}</option>
                }
              </select>
            </label>
          } @else {
            <label class="wide">
              Organisationsdefinition
              <select name="custom-blueprint" [(ngModel)]="customDefinitionKey" (ngModelChange)="resetCustomCounts()" required>
                @for (blueprint of baseBlueprints(); track blueprint.definition_key + ':' + blueprint.version) {
                  <option [value]="blueprint.definition_key">{{ blueprint.definition_key }} · v{{ blueprint.version }}</option>
                }
              </select>
            </label>
            <fieldset class="custom-grid wide">
              <legend>Team-Blueprint-Counts</legend>
              @for (option of customOptions(); track option.key) {
                <label>
                  {{ option.title }}
                  <input
                    type="number"
                    [name]="'custom-count-' + option.key"
                    [(ngModel)]="customCounts[option.key]"
                    min="0"
                    [max]="option.maximum"
                    step="1"
                  />
                  <small>{{ option.repeatable ? 'wiederholbar' : 'Singleton' }} · max. {{ option.maximum }}</small>
                </label>
              }
            </fieldset>
            <label class="wide">
              Begründung der Ausnahme
              <textarea name="custom-reason" [(ngModel)]="customReason" maxlength="512" required></textarea>
            </label>
            <article class="impact wide">
              <h3>Explizite Custom-N-Ausnahme</h3>
              <p><strong>{{ customTeamCount() }}</strong> Teams gewählt; erlaubt sind 2 bis {{ customMaxTeams() }}. Die Ausnahme wird an Principal, Projekt, Definition, Policy und exakte Counts gebunden und erst bei der Instanziierung einmalig verbraucht.</p>
              @if (missingCustomCapabilities().length) {
                <p class="warning"><strong>Nicht enthalten:</strong> {{ missingCustomCapabilities().join(', ') }}</p>
              }
            </article>
          }

          @if (selectedBlueprint(); as blueprint) {
            <article class="impact wide">
              <h3>Aktivierungs- und Scale-out-Auswirkung</h3>
              <p>{{ blueprint.description || 'Die Komposition wird serverseitig gegen Fähigkeiten, Rollen, Grenzen und Policies geprüft.' }}</p>
              <ul>
                @for (item of blueprint.activation_summary || []; track item) { <li>{{ item }}</li> }
              </ul>
              <p><strong>Erwartete Fähigkeiten:</strong> {{ (blueprint.capabilities || []).join(', ') || 'werden im Dry-run aufgelöst' }}</p>
            </article>
          }

          <div class="actions wide">
            <button type="submit" [disabled]="state.mutating() || !canCompile()">Dry-run erstellen</button>
          </div>
        </form>
      }

      @if (step >= 2 && state.compilePlan(); as plan) {
        <section class="preview" aria-labelledby="compile-preview-heading">
          <h3 id="compile-preview-heading">Gebundener Instanziierungsplan</h3>
          <dl class="metrics">
            <div><dt>Teams</dt><dd>{{ plan.team_count }}</dd></div>
            <div><dt>Units</dt><dd>{{ plan.unit_count }}</dd></div>
            <div><dt>Hierarchiekanten</dt><dd>{{ plan.hierarchy_edge_count }}</dd></div>
            <div><dt>Organisationskanten</dt><dd>{{ plan.relation_edge_count }}</dd></div>
            <div><dt>Role Slots</dt><dd>{{ plan.role_slot_count }}</dd></div>
          </dl>

          <div class="columns">
            <article>
              <h4>Geplante Writes</h4>
              <ul>@for (write of plan.planned_writes; track write) { <li>{{ write }}</li> }</ul>
            </article>
            <article>
              <h4>Pflichtrollen &amp; Fähigkeiten</h4>
              @if (!plan.capability_gaps.length && !plan.unfilled_required_slots.length) {
                <p class="ok">Keine Pflichtlücke erkannt.</p>
              }
              <ul>
                @for (gap of plan.capability_gaps; track gap) { <li class="blocker">Fähigkeit: {{ gap }}</li> }
                @for (slot of plan.unfilled_required_slots; track slot) { <li class="warning">Unbesetzt: {{ slot }}</li> }
              </ul>
            </article>
          </div>

          @if (budgetEntries(plan.budget_assumptions).length) {
            <h4>Budgetannahmen</h4>
            <dl class="budget">
              @for (entry of budgetEntries(plan.budget_assumptions); track entry[0]) {
                <div><dt>{{ entry[0] }}</dt><dd>{{ entry[1] }}</dd></div>
              }
            </dl>
          }

          @if (plan.diagnostics.length) {
            <ul class="diagnostics" aria-label="Dry-run-Diagnosen">
              @for (diagnostic of plan.diagnostics; track diagnostic.reason_code + diagnostic.message) {
                <li [attr.data-severity]="diagnostic.severity">
                  <strong>{{ diagnostic.severity }}</strong> · {{ diagnostic.message }}
                  <small>{{ diagnostic.reason_code }}</small>
                </li>
              }
            </ul>
          }

          <p class="digest"><strong>Plan-Digest:</strong> <code>{{ plan.plan_digest }}</code></p>
          <div class="actions">
            <button type="button" class="secondary" (click)="step = 1">Ändern</button>
            <button type="button" (click)="step = 3" [disabled]="hasBlockers()">Zur Bestätigung</button>
          </div>
        </section>
      }

      @if (step === 3 && state.compilePlan(); as plan) {
        <section class="confirmation" aria-labelledby="instantiate-heading">
          <h3 id="instantiate-heading">Bewusst instanziieren</h3>
          <p>Es werden Definition und Instanzen geschrieben. Worker oder Tasks werden dadurch nicht gestartet.</p>
          <label>
            Gebundener Organization-Admin-Grant
            <input type="password" [(ngModel)]="adminGrant" autocomplete="off" />
          </label>
          <label class="confirm">
            <input type="checkbox" [(ngModel)]="confirmed" />
            Ich bestätige Revision <code>{{ plan.definition_revision }}</code> und den angezeigten Dry-run.
          </label>
          <div class="actions">
            <button type="button" class="secondary" (click)="step = 2">Zurück</button>
            <button type="button" (click)="instantiate()" [disabled]="!confirmed || !adminGrant.trim() || state.mutating()">
              Organisation instanziieren
            </button>
          </div>
        </section>
      }
    </section>
  `,
  styles: [`
    .setup { display: grid; gap: 1rem; max-width: 1080px; }
    h2, h3, h4, p { margin-top: 0; }
    .eyebrow { color: #76a9ff; font-size: .75rem; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; }
    .steps { display: flex; flex-wrap: wrap; gap: .5rem; }
    .steps span { border: 1px solid #344567; border-radius: 999px; color: #a8b8d8; padding: .35rem .75rem; }
    .steps .active { background: #173a6b; border-color: #5c9dff; color: #fff; }
    .form-grid { display: grid; gap: 1rem; grid-template-columns: repeat(2, minmax(0, 1fr)); }
    label { display: grid; gap: .35rem; color: #c8d5ee; font-weight: 600; }
    input, select, textarea { background: #111a2c; border: 1px solid #3d4e70; border-radius: .45rem; color: #f5f8ff; padding: .65rem; }
    textarea { min-height: 5rem; resize: vertical; }
    .wide { grid-column: 1 / -1; }
    .custom-grid { border: 1px solid #314463; border-radius: .7rem; display: grid; gap: .8rem; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); padding: 1rem; }
    .custom-grid legend { color: #c8d5ee; font-weight: 700; padding: 0 .4rem; }
    .custom-grid small { color: #8ea3c6; font-weight: 400; }
    .impact, .preview, .confirmation { background: #101a2d; border: 1px solid #314463; border-radius: .8rem; padding: 1rem; }
    .metrics, .budget { display: grid; gap: .6rem; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); }
    .metrics div, .budget div { background: #0c1424; border-radius: .5rem; padding: .7rem; }
    dt { color: #9eb0cf; font-size: .78rem; } dd { margin: .25rem 0 0; font-weight: 700; }
    .columns { display: grid; gap: 1rem; grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .actions { display: flex; gap: .6rem; justify-content: flex-end; }
    button { background: #2f76d2; border: 0; border-radius: .45rem; color: white; cursor: pointer; padding: .65rem 1rem; }
    button.secondary { background: #263651; } button:disabled { cursor: not-allowed; opacity: .5; }
    .diagnostics { display: grid; gap: .4rem; list-style: none; padding: 0; }
    .diagnostics li { border-left: 4px solid #6e8bb8; background: #0c1424; padding: .6rem; }
    .diagnostics li[data-severity='blocker'], .blocker { border-color: #ff6f7d; color: #ffc0c6; }
    .diagnostics li[data-severity='warning'], .warning { border-color: #f0b44c; color: #ffdda0; }
    .diagnostics small { display: block; color: #8ea3c6; }
    .ok { color: #77d6a2; } .digest { overflow-wrap: anywhere; }
    .confirmation { display: grid; gap: 1rem; } .confirm { display: flex; align-items: flex-start; font-weight: 400; }
    @media (max-width: 760px) { .form-grid, .columns { grid-template-columns: 1fr; } }
  `],
})
export class OrganizationSetupComponent {
  readonly state = inject(OrganizationTopologyStateService);
  readonly productionBlueprints = computed(() => this.state.blueprints().filter(blueprint => !blueprint.test_only));
  readonly baseBlueprints = computed(() => {
    const unique = new Map<string, OrganizationBlueprintSummary>();
    for (const blueprint of this.productionBlueprints()) {
      if (!unique.has(blueprint.definition_key)) unique.set(blueprint.definition_key, blueprint);
    }
    return [...unique.values()];
  });
  readonly supportedCounts = computed(() => {
    const fromServer = this.productionBlueprints().map(item => item.team_count).filter(count => count >= 5 && count <= 10);
    return [...new Set(fromServer.length ? fromServer : [5, 6, 7, 8, 9, 10])].sort((left, right) => left - right);
  });

  step = 1;
  title = 'Enterprise Produktorganisation';
  compositionMode: 'standard' | 'custom' = 'standard';
  teamCount = 8;
  blueprintKey = '';
  customDefinitionKey = '';
  customCounts: Record<string, number> = {};
  customReason = 'Bewusste benutzerdefinierte Teamzusammensetzung';
  adminGrant = '';
  confirmed = false;

  constructor() {
    effect(() => {
      const plans = this.productionBlueprints();
      if (!this.blueprintKey && plans.length) {
        const recommended = plans.find(item => item.recommended)
          ?? plans.find(item => item.team_count === 8)
          ?? plans[0];
        this.blueprintKey = recommended.key;
        this.customDefinitionKey = recommended.definition_key;
        this.teamCount = recommended.team_count || 8;
      }
      if (this.state.compilePlan()) this.step = Math.max(this.step, 2);
    });
  }

  selectForCount(): void {
    const candidate = this.productionBlueprints().find(item => item.team_count === Number(this.teamCount));
    if (candidate) this.blueprintKey = candidate.key;
    this.state.compilePlan.set(null);
    this.step = 1;
  }

  changeCompositionMode(): void {
    this.state.compilePlan.set(null);
    this.step = 1;
    if (this.compositionMode === 'custom') this.resetCustomCounts();
  }

  resetCustomCounts(): void {
    this.customCounts = Object.fromEntries(
      this.customOptions().map(option => [
        option.key,
        option.key === 'enterprise_product_delivery_scrum' ? 2 : 0,
      ]),
    );
    this.state.compilePlan.set(null);
    this.step = 1;
  }

  selectedBlueprint() {
    return this.compositionMode === 'standard'
      ? this.productionBlueprints().find(blueprint => blueprint.key === this.blueprintKey) ?? null
      : this.baseBlueprints().find(blueprint => blueprint.definition_key === this.customDefinitionKey) ?? null;
  }

  customOptions() {
    return this.selectedBlueprint()?.custom_team_blueprints ?? [];
  }

  customTeamCount(): number {
    return Object.values(this.customCounts).reduce((sum, value) => sum + Math.max(0, Number(value) || 0), 0);
  }

  customMaxTeams(): number {
    return this.selectedBlueprint()?.custom_team_count_max ?? 2;
  }

  missingCustomCapabilities(): readonly string[] {
    const labels: Readonly<Record<string, string>> = {
      research_and_discovery: 'Research',
      platform_devops_sre: 'Platform/DevOps/SRE',
      quality_security_release: 'Quality/Security/Release',
      architecture_governance: 'Architecture Governance',
      proof_of_concept: 'Proof of Concept',
    };
    return Object.entries(labels)
      .filter(([key]) => Number(this.customCounts[key] || 0) < 1)
      .map(([, label]) => label);
  }

  canCompile(): boolean {
    if (!this.title.trim()) return false;
    if (this.compositionMode === 'standard') return Boolean(this.blueprintKey);
    const total = this.customTeamCount();
    return Boolean(
      this.customDefinitionKey
      && this.customReason.trim()
      && total >= 2
      && total <= this.customMaxTeams()
    );
  }

  compile(): void {
    if (!this.canCompile()) return;
    this.confirmed = false;
    this.adminGrant = '';
    if (this.compositionMode === 'standard') {
      this.state.compile({
        blueprint_key: this.blueprintKey,
        title: this.title.trim(),
        team_count: Number(this.teamCount),
      });
      return;
    }
    const counts = Object.fromEntries(
      Object.entries(this.customCounts)
        .map(([key, value]) => [key, Number(value)] as const)
        .filter(([, value]) => Number.isInteger(value) && value > 0),
    );
    const blueprint = this.selectedBlueprint();
    if (!blueprint) return;
    this.state.compileCustom(
      blueprint.definition_key,
      blueprint.version,
      this.title.trim(),
      counts,
      this.customReason.trim(),
    );
  }

  instantiate(): void {
    if (!this.confirmed) return;
    this.state.instantiate(this.adminGrant);
  }

  hasBlockers(): boolean {
    const plan = this.state.compilePlan();
    return Boolean(plan && (plan.capability_gaps.length || plan.diagnostics.some(item => item.severity === 'blocker')));
  }

  budgetEntries(value: Readonly<Record<string, number>>): readonly [string, number][] {
    return Object.entries(value);
  }
}
