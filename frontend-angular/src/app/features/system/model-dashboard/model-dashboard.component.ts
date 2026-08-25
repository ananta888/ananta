import { JsonPipe } from '@angular/common';
import { Component, HostListener, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { DashboardFeatureFlagStore } from '../../dashboard-foundation/dashboard-feature-flags';
import { ModelConsumer } from './model-catalog.client';
import { ModelDashboardStore, ModelDashboardTab } from './model-dashboard.store';

@Component({
  standalone: true,
  selector: 'app-model-dashboard',
  imports: [FormsModule, JsonPipe],
  providers: [ModelDashboardStore],
  template: `
    @if (features.angularModelDashboard()) {
      <section class="model-dashboard" aria-labelledby="model-dashboard-title" data-testid="model-dashboard">
        <header>
          <div>
            <p class="eyebrow">Hub Model Control Plane</p>
            <h2 id="model-dashboard-title">Modelle</h2>
            <p>Bestand, zentrale Zuweisungen, Fallbacks und atomare Änderungen.</p>
            <div class="header-facts" aria-label="Katalogstatus">
              <span>Katalog r{{ store.inventory()?.catalog_revision ?? '–' }}</span>
              <span>Routing r{{ store.authoritativeRouting()?.revision ?? '–' }}</span>
              <span>{{ store.inventory()?.models?.length ?? 0 }} Modelle</span>
              @if (store.dirty()) { <strong>Ungespeicherte Änderungen</strong> }
            </div>
          </div>
          @if (store.canRefresh()) {
            <button type="button" (click)="store.refresh()" [disabled]="store.loading()">
              Quellen aktualisieren
            </button>
          }
        </header>

        <nav class="model-tabs" aria-label="Modellkonfiguration" role="tablist">
          @for (tab of tabs; track tab.id) {
            <button type="button" role="tab" [attr.aria-selected]="store.activeTab() === tab.id"
              [class.active]="store.activeTab() === tab.id" (click)="store.activeTab.set(tab.id)">
              {{ tab.label }}
            </button>
          }
        </nav>

        @if (store.error()) { <p class="model-error" role="alert">{{ store.error() }}</p> }
        @if (store.loading()) { <p role="status">Modelldaten werden geladen …</p> }

        @if (store.activeTab() === 'overview') {
          <section role="tabpanel" aria-label="Modellübersicht">
            <div class="filters" aria-label="Modellfilter">
              <label>Suche
                <input type="search" [ngModel]="store.search()" (ngModelChange)="store.search.set($event)"
                  placeholder="Name, Modell, Provider, Executor oder Profil" />
              </label>
              <label>Runtime
                <select [ngModel]="store.runtimeFilter()" (ngModelChange)="store.runtimeFilter.set($event)">
                  <option value="all">Alle</option><option value="local">Lokal</option>
                  <option value="cloud">Cloud</option><option value="remote">Remote</option>
                  <option value="unknown">Unbekannt</option>
                </select>
              </label>
              <label>Verfügbarkeit
                <select [ngModel]="store.availabilityFilter()" (ngModelChange)="store.availabilityFilter.set($event)">
                  <option value="all">Alle</option><option value="available">Verfügbar</option>
                  <option value="degraded">Degraded</option><option value="unavailable">Nicht verfügbar</option>
                  <option value="unknown">Unbestätigt</option>
                </select>
              </label>
              <label>Quelle
                <select [ngModel]="store.sourceFilter()" (ngModelChange)="store.sourceFilter.set($event)">
                  <option value="all">Alle</option><option value="configured">Konfiguriert</option>
                  <option value="discovered">Entdeckt</option><option value="observed_runtime">Beobachtet</option>
                  <option value="remote">Remote</option>
                </select>
              </label>
            </div>

            @for (source of store.inventory()?.sources ?? []; track source.source_id) {
              @if (source.status !== 'healthy') {
                <p class="provider-failure" role="status">
                  <strong>{{ source.source_id }}</strong>: {{ source.status }}
                  @if (source.reason_code) { – {{ source.reason_code }} }
                </p>
              }
            }
            <p class="muted">{{ store.filteredModels().length }} Treffer; maximal 500 werden gleichzeitig dargestellt.</p>
            <div class="model-table-wrap">
              <table class="model-table">
                <thead><tr><th>Modell</th><th>Provider / Executor</th><th>Runtime</th><th>Status</th><th>Quellen</th><th>Details</th></tr></thead>
                <tbody>
                  @for (model of store.visibleModels(); track model.provider_id + ':' + model.model_id + ':' + model.executor_id) {
                    <tr>
                      <td><strong>{{ model.display_name }}</strong><code>{{ model.model_id }}</code></td>
                      <td><span>{{ model.provider_id }}</span><code>{{ model.executor_id }}</code></td>
                      <td>{{ model.runtime }}</td>
                      <td>{{ model.availability }} / {{ model.health }}</td>
                      <td>{{ model.source_kinds.join(', ') }}</td>
                      <td>
                        <details>
                          <summary>Öffnen</summary>
                          <p>Profile: {{ model.profile_ids.join(', ') || 'keine' }}</p>
                          <p>Capabilities: {{ capabilityLabels(model) }}</p>
                          <p>Listing: {{ model.listing_supported ? 'unterstützt' : 'nicht unterstützt' }}</p>
                          <p>Installiert: {{ model.installed === null ? 'unbekannt' : model.installed }}</p>
                          <p>Verwendet von: {{ model.used_by_consumers.join(', ') || 'keinem Consumer' }}</p>
                          @if (model.conflicts.length) { <p class="warning">Konflikte: {{ model.conflicts.join(', ') }}</p> }
                        </details>
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          </section>
        }

        @if (store.activeTab() === 'assignments') {
          <section role="tabpanel" aria-label="Zuweisungsmatrix">
            <p>Globale Werte sind editierbar; engere Scopes bleiben im Hub erhalten und haben Vorrang.</p>
            @for (category of store.consumerCategories(); track category.category) {
              <section class="assignment-group">
                <h3>{{ category.category }}</h3>
                <div class="assignment-table-wrap"><table class="assignment-table">
                  <thead><tr><th>Consumer</th><th>Modus</th><th>Primärprofil</th><th>Fallback</th><th>Effektiv</th></tr></thead>
                  <tbody>
                    @for (consumer of category.consumers; track consumer.consumer_id) {
                      <tr>
                        <td><strong>{{ consumer.label }}</strong><code>{{ consumer.consumer_id }}</code>
                          <small>{{ consumer.required_capabilities.join(', ') || 'keine harten Capabilities' }}</small>
                          @if (!consumer.routable) { <small class="warning">{{ consumer.non_routable_reason }}</small> }
                        </td>
                        <td><select [attr.aria-label]="'Modus für ' + consumer.label"
                          [disabled]="!consumer.routable"
                          [ngModel]="store.globalAssignment(consumer.consumer_id)?.mode ?? 'inherit'"
                          (ngModelChange)="changeMode(consumer, $event)">
                          <option value="inherit">Erben</option><option value="profile">Profil</option><option value="disabled">Deaktiviert</option>
                        </select></td>
                        <td><select [attr.aria-label]="'Profil für ' + consumer.label"
                          [disabled]="!consumer.routable || store.globalAssignment(consumer.consumer_id)?.mode !== 'profile'"
                          [ngModel]="store.globalAssignment(consumer.consumer_id)?.profile_id ?? ''"
                          (ngModelChange)="store.setConsumerMode(consumer.consumer_id, 'profile', $event)">
                          <option value="">Profil wählen</option>
                          @for (option of store.profileOptions(); track option.profileId) {
                            <option [value]="option.profileId">{{ option.profileId }} — {{ option.model.model_id }}</option>
                          }
                        </select></td>
                        <td><select [attr.aria-label]="'Fallback für ' + consumer.label"
                          [disabled]="!consumer.routable"
                          [ngModel]="store.globalAssignment(consumer.consumer_id)?.fallback_group_id ?? ''"
                          (ngModelChange)="store.setConsumerFallback(consumer.consumer_id, $event)">
                          <option value="">Kein zentraler Fallback</option>
                          @for (group of store.draftRouting()?.fallback_groups ?? []; track group.group_id) {
                            <option [value]="group.group_id">{{ group.group_id }}</option>
                          }
                        </select></td>
                        <td><button type="button" class="button-outline" [disabled]="!consumer.routable"
                          (click)="store.dryRun(consumer.consumer_id)">Route prüfen</button></td>
                      </tr>
                    }
                  </tbody>
                </table></div>
              </section>
            }
            @if (store.effectiveRoute(); as route) {
              <aside class="route-preview" aria-live="polite">
                <h3>Effektive Route: {{ route.consumer_id }}</h3>
                <p><strong>{{ route.resolved_profile_id || 'blockiert' }}</strong> via {{ route.assignment_source }}</p>
                <p>Kette: {{ route.candidate_profile_ids.join(' → ') || 'keine' }}</p>
                @for (decision of route.decisions; track $index) {
                  <p [class.warning]="!decision.accepted">{{ decision.source }}: {{ decision.profile_id || '–' }} — {{ decision.reason }}</p>
                }
              </aside>
            }
          </section>
        }

        @if (store.activeTab() === 'fallbacks') {
          <section role="tabpanel" aria-label="Fallback-Editor">
            <div class="row-controls">
              <label>Neue Gruppen-ID <input [(ngModel)]="newGroupId" /></label>
              <button type="button" (click)="store.addFallbackGroup(newGroupId); newGroupId = ''">Gruppe anlegen</button>
            </div>
            @for (group of store.draftRouting()?.fallback_groups ?? []; track group.group_id) {
              <section class="fallback-card" [attr.aria-labelledby]="'fallback-' + group.group_id">
                <header><h3 [id]="'fallback-' + group.group_id">{{ group.group_id }}</h3>
                  <button type="button" class="danger-button" (click)="store.removeFallbackGroup(group.group_id)">Gruppe entfernen</button></header>
                <label>Gesamt-Retry-Budget
                  <input type="number" min="0" max="64" [ngModel]="group.max_total_retries"
                    (ngModelChange)="store.setGroupRetries(group.group_id, $event)" />
                </label>
                <div class="group-policy">
                  <label>Nach Ausschöpfung
                    <select [ngModel]="group.on_exhausted"
                      (ngModelChange)="store.setGroupExhaustion(group.group_id, $event)">
                      <option value="stop">Stoppen</option><option value="escalate">An Profil eskalieren</option>
                    </select>
                  </label>
                  @if (group.on_exhausted === 'escalate') {
                    <label>Eskalationsprofil
                      <select [ngModel]="group.escalation_profile_id ?? ''"
                        (ngModelChange)="store.setGroupExhaustion(group.group_id, 'escalate', $event)">
                        <option value="">Profil wählen</option>
                        @for (option of store.profileOptions(); track option.profileId) {
                          <option [value]="option.profileId" [disabled]="candidateProfileIds(group).includes(option.profileId)">
                            {{ option.profileId }}
                          </option>
                        }
                      </select>
                    </label>
                  }
                  <p class="muted">Policy-Blockierungen sind immer terminal und können hier nicht abgeschwächt werden.</p>
                </div>
                <ol>
                  @for (candidate of group.candidates; track candidate.profile_id; let index = $index) {
                    <li>
                      <div class="candidate-identity"><code>{{ candidate.profile_id }}</code>
                        <label>Retries <input type="number" min="0" max="8" [ngModel]="candidate.retry_budget"
                          (ngModelChange)="store.setCandidateRetry(group.group_id, index, $event)" /></label>
                      </div>
                      <details class="candidate-policy">
                        <summary>Bedingungen und Limits</summary>
                        <div class="candidate-fields">
                          <label>Fehlerklassen (kommagetrennt)
                            <input [ngModel]="candidate.triggers.join(', ')"
                              (ngModelChange)="store.setCandidateTriggers(group.group_id, index, $event)"
                              placeholder="timeout, connection_error" />
                          </label>
                          <label>Max. Kontext-Tokens
                            <input type="number" min="1" [ngModel]="candidate.max_context_tokens ?? ''"
                              (ngModelChange)="store.setCandidateLimit(group.group_id, index, 'max_context_tokens', $event)" />
                          </label>
                          <label>Max. Kosten pro Schritt
                            <input type="number" min="0" step="0.001" [ngModel]="candidate.max_estimated_cost_per_step ?? ''"
                              (ngModelChange)="store.setCandidateLimit(group.group_id, index, 'max_estimated_cost_per_step', $event)" />
                          </label>
                          <label class="check-field"><input type="checkbox" [ngModel]="candidate.requires_tools"
                            (ngModelChange)="store.setCandidateRequirement(group.group_id, index, 'requires_tools', $event)" /> Tool-Calling erforderlich</label>
                          <label class="check-field"><input type="checkbox" [ngModel]="candidate.requires_json"
                            (ngModelChange)="store.setCandidateRequirement(group.group_id, index, 'requires_json', $event)" /> JSON erforderlich</label>
                          <label class="check-field"><input type="checkbox" [ngModel]="candidate.cloud_allowed"
                            (ngModelChange)="store.setCandidateRequirement(group.group_id, index, 'cloud_allowed', $event)" /> Cloud für diesen Kandidaten erlaubt</label>
                        </div>
                      </details>
                      <div class="candidate-actions" [attr.aria-label]="'Reihenfolge für ' + candidate.profile_id">
                        <button type="button" [disabled]="index === 0" (click)="store.moveCandidate(group.group_id, index, -1)" aria-label="Nach oben">↑</button>
                        <button type="button" [disabled]="index === group.candidates.length - 1" (click)="store.moveCandidate(group.group_id, index, 1)" aria-label="Nach unten">↓</button>
                        <button type="button" (click)="store.removeCandidate(group.group_id, index)" aria-label="Kandidat entfernen">Entfernen</button>
                      </div>
                    </li>
                  }
                </ol>
                <div class="row-controls">
                  <label>Profil hinzufügen
                    <select #candidateSelect><option value="">Profil wählen</option>
                      @for (option of store.profileOptions(); track option.profileId) {
                        <option [value]="option.profileId">{{ option.profileId }}</option>
                      }
                    </select>
                  </label>
                  <button type="button" (click)="store.addCandidate(group.group_id, candidateSelect.value); candidateSelect.value = ''">Hinzufügen</button>
                </div>
              </section>
            }
          </section>
        }

        @if (store.activeTab() === 'changes') {
          <section role="tabpanel" aria-label="Änderungen anwenden" class="changes-panel">
            <h3>Atomarer Änderungsstand</h3>
            <p>Authoritative Revision: {{ store.authoritativeRouting()?.revision ?? '–' }}</p>
            <p>{{ store.dirty() ? 'Der Draft weicht vom Hub ab.' : 'Keine ungespeicherten Änderungen.' }}</p>
            <div class="row-controls">
              <button type="button" (click)="store.validate()" [disabled]="!store.canValidate() || !store.dirty()">Serverseitig validieren</button>
              <button type="button" (click)="store.save()" [disabled]="!store.canMutate() || !store.dirty() || store.saving()">Validieren und atomar speichern</button>
              <button type="button" class="button-outline" (click)="store.resetDraft()" [disabled]="!store.dirty()">Draft verwerfen</button>
              <button type="button" class="button-outline" (click)="store.load()">Neu laden</button>
              <button type="button" class="button-outline" (click)="store.exportRouting()" [disabled]="!store.canExport()">Exportieren</button>
            </div>
            <div class="template-panel">
              <label>Sichere Routing-Vorlage
                <select [(ngModel)]="selectedTemplateId">
                  <option value="">Vorlage wählen</option>
                  @for (template of store.templates(); track template.template_id) {
                    <option [value]="template.template_id" [disabled]="!template.applicable">
                      {{ template.label }}{{ template.applicable ? '' : ' (nicht anwendbar)' }}
                    </option>
                  }
                </select>
              </label>
              <button type="button" class="button-outline"
                [disabled]="!selectedTemplateId || !store.canMutate()"
                (click)="store.applyTemplate(selectedTemplateId)">Als lokalen Draft übernehmen</button>
              @if (selectedTemplate(); as template) {
                <p>{{ template.description }}</p>
                @for (issue of template.issues; track $index) {
                  <p class="muted">{{ issue.reason_code }}{{ issue.reference ? ': ' + issue.reference : '' }}</p>
                }
              }
            </div>
            @if (store.validation(); as report) {
              <div [class.validation-ok]="report.valid" [class.model-error]="!report.valid" role="status">
                <strong>{{ report.valid ? 'Validierung erfolgreich' : 'Validierung fehlgeschlagen' }}</strong>
                @for (issue of report.issues; track $index) { <p>{{ issue.reason_code }} {{ issue.reference || '' }}</p> }
              </div>
            }
            @if (store.conflictRevision()) {
              <p class="model-error" role="alert">Aktuelle Hub-Revision: {{ store.conflictRevision() }}. Neu laden und vergleichen.</p>
            }
            <label class="import-field">Routing-Export importieren
              <input type="file" accept="application/json,.json" (change)="readImport($event)" />
            </label>
            @if (store.importPreview(); as preview) {
              <div class="route-preview">
                <h4>Import-Vorschau</h4><pre>{{ preview | json }}</pre>
                <button type="button" (click)="store.applyImported()"
                  [disabled]="!store.canMutate() || preview['applicable'] !== true">Explizit bestätigen und anwenden</button>
              </div>
            }
          </section>
        }
      </section>
    }
  `,
  styles: [`
    .model-dashboard { display: grid; gap: 1rem; min-width: 0; }
    .model-dashboard > header { align-items: start; background: linear-gradient(120deg, #173c48, #286773); border-radius: 1rem; color: white; display: flex; justify-content: space-between; padding: 1.2rem; }
    .eyebrow { font-size: .72rem; font-weight: 800; letter-spacing: .15em; text-transform: uppercase; }
    h2 { font-family: Georgia, serif; margin: .2rem 0; }
    .header-facts, .row-controls, .candidate-actions { align-items: center; display: flex; flex-wrap: wrap; gap: .5rem; }
    .header-facts span, .header-facts strong { border: 1px solid #9fc4c8; border-radius: 99rem; font-size: .75rem; padding: .2rem .5rem; }
    .model-tabs { border-bottom: 1px solid #b8c8c9; display: flex; gap: .25rem; overflow-x: auto; }
    .model-tabs button { background: transparent; border: 0; border-bottom: 3px solid transparent; padding: .7rem 1rem; }
    .model-tabs button.active { border-bottom-color: #286773; font-weight: 700; }
    .filters { display: grid; gap: .7rem; grid-template-columns: minmax(16rem, 2fr) repeat(3, minmax(9rem, 1fr)); margin-bottom: 1rem; }
    label { display: grid; font-size: .82rem; gap: .25rem; }
    input, select { border: 1px solid #8da6a7; border-radius: .35rem; min-height: 2.2rem; padding: .35rem; }
    .model-table-wrap, .assignment-table-wrap { max-width: 100%; overflow: auto; }
    table { border-collapse: collapse; width: 100%; }
    th, td { border-bottom: 1px solid #cfdbdc; padding: .6rem; text-align: left; vertical-align: top; }
    td code, td small { display: block; margin-top: .25rem; }
    .assignment-group, .fallback-card, .route-preview, .changes-panel, .template-panel { border: 1px solid #b8c8c9; border-radius: .8rem; margin-bottom: 1rem; padding: 1rem; }
    .fallback-card > header { align-items: center; display: flex; justify-content: space-between; }
    .fallback-card li { align-items: start; background: #f1f6f4; display: grid; gap: .7rem; grid-template-columns: minmax(12rem, .7fr) minmax(18rem, 1.3fr) auto; margin-bottom: .6rem; padding: .8rem; }
    .candidate-identity, .candidate-fields, .group-policy { display: grid; gap: .5rem; }
    .candidate-policy summary { cursor: pointer; font-weight: 700; margin-bottom: .5rem; }
    .candidate-fields { grid-template-columns: repeat(2, minmax(10rem, 1fr)); }
    .check-field { align-items: center; display: flex; gap: .4rem; }
    .check-field input { min-height: auto; }
    .provider-failure, .model-error { background: #fff0eb; border-left: 4px solid #9f3029; padding: .7rem; }
    .warning { color: #8b3d12; }.validation-ok { background: #e5f6eb; border-left: 4px solid #287b45; padding: .7rem; }
    .danger-button { background: #8d2924; color: white; }.import-field { margin-top: 1rem; }
    pre { max-height: 20rem; overflow: auto; white-space: pre-wrap; }
    button:focus-visible, input:focus-visible, select:focus-visible, summary:focus-visible { outline: 3px solid #1677c8; outline-offset: 2px; }
    @media (max-width: 850px) { .filters { grid-template-columns: 1fr 1fr; }.fallback-card li { grid-template-columns: 1fr; } }
    @media (max-width: 520px) { .model-dashboard > header { flex-direction: column; gap: .8rem; }.filters { grid-template-columns: 1fr; } }
  `],
})
export class ModelDashboardComponent implements OnInit {
  readonly features = inject(DashboardFeatureFlagStore);
  readonly store = inject(ModelDashboardStore);
  readonly tabs: readonly { id: ModelDashboardTab; label: string }[] = [
    { id: 'overview', label: 'Übersicht' }, { id: 'assignments', label: 'Zuweisungen' },
    { id: 'fallbacks', label: 'Fallbacks' }, { id: 'changes', label: 'Änderungen' },
  ];
  newGroupId = '';
  selectedTemplateId = '';

  ngOnInit(): void {
    this.features.ensureLoaded().subscribe(flags => {
      if (flags.angularModelDashboard) this.store.load();
    });
  }

  @HostListener('window:beforeunload', ['$event'])
  preventDirtyNavigation(event: BeforeUnloadEvent): void {
    if (this.store.dirty()) event.preventDefault();
  }

  changeMode(consumer: ModelConsumer, mode: 'inherit' | 'profile' | 'disabled'): void {
    const firstProfile = this.store.profileOptions()[0]?.profileId ?? '';
    this.store.setConsumerMode(consumer.consumer_id, mode, mode === 'profile' ? firstProfile : '');
  }

  capabilityLabels(model: { capabilities: readonly { capability_id: string; value: string; evidence: string }[] }): string {
    return model.capabilities.map(item => `${item.capability_id}: ${item.value} (${item.evidence})`).join(', ') || 'unbekannt';
  }

  candidateProfileIds(group: { candidates: readonly { profile_id: string }[] }): readonly string[] {
    return group.candidates.map(candidate => candidate.profile_id);
  }

  selectedTemplate() {
    return this.store.templates().find(template => template.template_id === this.selectedTemplateId);
  }

  readImport(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file || file.size > 2_000_000) return;
    file.text().then(value => this.store.previewImported(JSON.parse(value))).catch(() => {
      this.store.error.set('Importdatei ist kein gültiges JSON.');
    });
  }
}
