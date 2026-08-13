import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
} from '@angular/core';

import type {
  VpGraph,
  VpStep,
} from '../../visual-process/visual-process-api.service';
import type {
  CaseFlowAgentBindingCatalogReadModel,
  CaseFlowBindingResourceKind,
} from './caseflow-agent-binding-catalog.service';
import {
  CASEFLOW_AGENT_ICON_ALLOWLIST,
  type CaseFlowAgentIcon,
  type CaseFlowContextBindingRef,
  type CaseFlowContextResourceType,
  type CaseFlowPersonalityResourceType,
  isAllowedCaseFlowAgentIcon,
} from './caseflow-agent-canvas.models';
import { projectAgentCanvas } from './caseflow-agent-canvas.mapper';
import {
  type CaseFlowAgentBindingDraft,
  type CaseFlowAgentBindingCatalog,
  setAgentBindings,
  setAgentRoleAndIcon,
  validateBindingDraft,
} from './caseflow-agent-graph.commands';
import {
  type CaseFlowAgentNeighborhood,
  selectCaseFlowAgentNeighborhood,
} from './caseflow-agent-neighborhood.selector';
import {
  CASEFLOW_AGENT_ROLE_CATEGORIES,
  CASEFLOW_AGENT_ROLES_BY_CATEGORY,
  type CaseFlowAgentRoleCategory,
  type CaseFlowAgentRoleId,
  type CaseFlowAgentRoleSelection,
  isCaseFlowAgentRoleId,
  roleDefinitionFor,
  validateRoleSelection,
} from './caseflow-role-catalog';

type OptionalPersonalityType = CaseFlowPersonalityResourceType | '';
type OptionalContextType = CaseFlowContextResourceType | '';

interface CaseFlowAgentInspectorDraft {
  readonly roleId: CaseFlowAgentRoleId;
  readonly customRoleName: string;
  readonly icon: CaseFlowAgentIcon;
  readonly skillProfileId: string;
  readonly personalityType: OptionalPersonalityType;
  readonly personalityId: string;
  readonly contextBindings: readonly CaseFlowContextBindingRef[];
  readonly pendingContextType: OptionalContextType;
  readonly pendingContextId: string;
  readonly modelRole: string;
  readonly modelProfileId: string;
  readonly fallbackGroupId: string;
}

const PERSONALITY_RESOURCE_TYPES: readonly CaseFlowPersonalityResourceType[] = [
  'instruction_layer',
  'agent_profile',
];
const CONTEXT_RESOURCE_TYPES: readonly CaseFlowContextResourceType[] = [
  'context_source',
  'context_profile',
];

const RESOURCE_LABELS: Readonly<Record<CaseFlowBindingResourceKind, string>> = {
  skill_profile: 'Skill-Profil',
  agent_profile: 'Agent-Profil',
  instruction_layer: 'Instruction Layer',
  context_profile: 'Kontextprofil',
  context_source: 'Kontextquelle',
  model_profile: 'Modellprofil',
  model_role: 'Modellrolle',
  fallback_group: 'Fallback-Gruppe',
};

let inspectorSequence = 0;

/**
 * Edits one selected canonical VpStep through the focused CaseFlow commands.
 * Catalog loading and persistence deliberately stay outside this component.
 */
@Component({
  selector: 'app-caseflow-agent-node-inspector',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <aside class="node-inspector" [attr.aria-labelledby]="titleId">
      <header class="inspector-header">
        <p class="eyebrow">Agent-Konfiguration</p>
        <h2 [id]="titleId">{{ selectedStep?.label || 'Kein Agent gewählt' }}</h2>
      </header>

      @if (selectionError) {
        <p class="message error" role="alert">{{ selectionError }}</p>
      } @else if (!selectedStep || !draft) {
        <p class="message" role="status">Wähle einen Agenten im Canvas aus.</p>
      } @else {
        <section class="inspector-section" aria-labelledby="identity-heading">
          <h3 id="identity-heading">Rolle und Darstellung</h3>

          <label [for]="roleControlId">Rolle</label>
          <select
            [id]="roleControlId"
            data-field="role"
            [value]="draft.roleId"
            (change)="changeRole($event)"
          >
            @for (category of roleCategories; track category) {
              <optgroup [label]="roleCategoryLabel(category)">
                @for (role of rolesFor(category); track role.id) {
                  <option [value]="role.id">{{ role.label }}</option>
                }
              </optgroup>
            }
          </select>

          @if (draft.roleId === 'custom') {
            <label [for]="customRoleControlId">Eigener Rollenname</label>
            <input
              [id]="customRoleControlId"
              data-field="custom-role"
              type="text"
              [value]="draft.customRoleName"
              (input)="changeCustomRoleName($event)"
            />
          }

          <label [for]="iconControlId">Icon</label>
          <select
            [id]="iconControlId"
            data-field="icon"
            [value]="draft.icon"
            (change)="changeIcon($event)"
          >
            @for (icon of icons; track icon) {
              <option [value]="icon">{{ icon }}</option>
            }
          </select>
        </section>

        <section class="inspector-section" aria-labelledby="bindings-heading">
          <h3 id="bindings-heading">Autorisierte Bindings</h3>
          <p class="section-help">
            Gespeichert werden ausschließlich IDs aus dem aktuellen Hub-Katalog.
          </p>

          <div class="field-group" data-resource-kind="skill_profile">
            <label [for]="skillControlId">Skill-Profil</label>
            <div class="field-with-action">
              <select
                [id]="skillControlId"
                data-field="skill-profile"
                [value]="draft.skillProfileId"
                [disabled]="!resourceSelectable('skill_profile')"
                [attr.aria-describedby]="skillStatusId"
                (change)="changeSkillProfile($event)"
              >
                <option value="">Keines</option>
                @for (id of skillProfileOptions; track id) {
                  <option [value]="id">{{ id }}</option>
                }
              </select>
              @if (draft.skillProfileId) {
                <button type="button" class="secondary" (click)="clearSkillProfile()">
                  Entfernen
                </button>
              }
            </div>
            <p
              class="resource-status"
              [id]="skillStatusId"
              [attr.data-state]="resourceState('skill_profile')"
            >{{ resourceStatus('skill_profile') }}</p>
          </div>

          <fieldset>
            <legend>Persönlichkeit / System-Instruktion</legend>
            <label [for]="personalityTypeControlId">Ressourcentyp</label>
            <select
              [id]="personalityTypeControlId"
              data-field="personality-type"
              [value]="draft.personalityType"
              (change)="changePersonalityType($event)"
            >
              <option value="">Keine</option>
              @for (type of personalityResourceTypes; track type) {
                <option [value]="type" [disabled]="!resourceReady(type)">
                  {{ resourceLabel(type) }}
                </option>
              }
            </select>

            <label [for]="personalityIdControlId">Autorisierte Ressource</label>
            <div class="field-with-action">
              <select
                [id]="personalityIdControlId"
                data-field="personality-id"
                [value]="draft.personalityId"
                [disabled]="!personalitySelectable"
                (change)="changePersonalityId($event)"
              >
                <option value="">Auswählen</option>
                @for (id of personalityOptions; track id) {
                  <option [value]="id">{{ id }}</option>
                }
              </select>
              @if (draft.personalityType || draft.personalityId) {
                <button type="button" class="secondary" (click)="clearPersonality()">
                  Entfernen
                </button>
              }
            </div>
            <ul class="resource-status-list" aria-label="Verfügbarkeit der Persönlichkeitskataloge">
              @for (type of personalityResourceTypes; track type) {
                <li [attr.data-resource-kind]="type" [attr.data-state]="resourceState(type)">
                  {{ resourceStatus(type) }}
                </li>
              }
            </ul>
          </fieldset>

          <fieldset>
            <legend>Kontext</legend>
            @if (draft.contextBindings.length) {
              <ul class="binding-list" aria-label="Ausgewählte Kontext-Bindings">
                @for (binding of draft.contextBindings; track bindingKey(binding); let index = $index) {
                  <li [attr.data-binding-id]="binding.resource_id">
                    <span>{{ resourceLabel(binding.resource_type) }}: {{ binding.resource_id }}</span>
                    <button
                      type="button"
                      class="secondary"
                      [attr.aria-label]="'Kontext-Binding ' + binding.resource_id + ' entfernen'"
                      (click)="removeContextBinding(index)"
                    >Entfernen</button>
                  </li>
                }
              </ul>
            } @else {
              <p class="empty-value">Keine Kontext-Bindings gewählt.</p>
            }

            <label [for]="contextTypeControlId">Ressourcentyp hinzufügen</label>
            <select
              [id]="contextTypeControlId"
              data-field="context-type"
              [value]="draft.pendingContextType"
              (change)="changePendingContextType($event)"
            >
              <option value="">Auswählen</option>
              @for (type of contextResourceTypes; track type) {
                <option [value]="type" [disabled]="!resourceReady(type)">
                  {{ resourceLabel(type) }}
                </option>
              }
            </select>

            <label [for]="contextIdControlId">Autorisierte Ressource</label>
            <div class="field-with-action">
              <select
                [id]="contextIdControlId"
                data-field="context-id"
                [value]="draft.pendingContextId"
                [disabled]="!pendingContextSelectable"
                (change)="changePendingContextId($event)"
              >
                <option value="">Auswählen</option>
                @for (id of pendingContextOptions; track id) {
                  <option [value]="id">{{ id }}</option>
                }
              </select>
              <button
                type="button"
                class="secondary"
                data-action="add-context"
                [disabled]="!canAddContext"
                (click)="addContextBinding()"
              >Hinzufügen</button>
            </div>
            <ul class="resource-status-list" aria-label="Verfügbarkeit der Kontextkataloge">
              @for (type of contextResourceTypes; track type) {
                <li [attr.data-resource-kind]="type" [attr.data-state]="resourceState(type)">
                  {{ resourceStatus(type) }}
                </li>
              }
            </ul>
          </fieldset>

          <div class="model-grid">
            <div class="field-group" data-resource-kind="model_role">
              <label [for]="modelRoleControlId">Modellrolle</label>
              <div class="field-with-action">
                <select
                  [id]="modelRoleControlId"
                  data-field="model-role"
                  [value]="draft.modelRole"
                  [disabled]="!resourceSelectable('model_role')"
                  (change)="changeModelRole($event)"
                >
                  <option value="">Keine</option>
                  @for (id of modelRoleOptions; track id) {
                    <option [value]="id">{{ id }}</option>
                  }
                </select>
                @if (draft.modelRole) {
                  <button type="button" class="secondary" (click)="clearModelRole()">Entfernen</button>
                }
              </div>
              <p class="resource-status" [attr.data-state]="resourceState('model_role')">
                {{ resourceStatus('model_role') }}
              </p>
            </div>

            <div class="field-group" data-resource-kind="model_profile">
              <label [for]="modelProfileControlId">Bevorzugtes Modellprofil</label>
              <div class="field-with-action">
                <select
                  [id]="modelProfileControlId"
                  data-field="model-profile"
                  [value]="draft.modelProfileId"
                  [disabled]="!resourceSelectable('model_profile')"
                  (change)="changeModelProfile($event)"
                >
                  <option value="">Keines</option>
                  @for (id of modelProfileOptions; track id) {
                    <option [value]="id">{{ id }}</option>
                  }
                </select>
                @if (draft.modelProfileId) {
                  <button type="button" class="secondary" (click)="clearModelProfile()">Entfernen</button>
                }
              </div>
              <p class="resource-status" [attr.data-state]="resourceState('model_profile')">
                {{ resourceStatus('model_profile') }}
              </p>
            </div>

            <div class="field-group" data-resource-kind="fallback_group">
              <label [for]="fallbackControlId">Fallback-Gruppe</label>
              <div class="field-with-action">
                <select
                  [id]="fallbackControlId"
                  data-field="fallback-group"
                  [value]="draft.fallbackGroupId"
                  [disabled]="!resourceSelectable('fallback_group')"
                  (change)="changeFallbackGroup($event)"
                >
                  <option value="">Keine</option>
                  @for (id of fallbackGroupOptions; track id) {
                    <option [value]="id">{{ id }}</option>
                  }
                </select>
                @if (draft.fallbackGroupId) {
                  <button type="button" class="secondary" (click)="clearFallbackGroup()">Entfernen</button>
                }
              </div>
              <p class="resource-status" [attr.data-state]="resourceState('fallback_group')">
                {{ resourceStatus('fallback_group') }}
              </p>
            </div>
          </div>
        </section>

        <section class="inspector-section relationships" aria-labelledby="relationships-heading">
          <h3 id="relationships-heading">Gerichtete Beziehungen</h3>
          <div class="relationship-grid">
            <div>
              <h4>Parents</h4>
              @if (neighborhood?.parents?.length) {
                <ul data-relations="parents">
                  @for (relation of neighborhood!.parents; track relation.edge_id) {
                    <li [attr.data-edge-id]="relation.edge_id">
                      <strong>{{ relation.peer_label }}</strong>
                      <span>{{ relation.peer_role }}</span>
                      <code>{{ relation.edge_id }}</code>
                    </li>
                  }
                </ul>
              } @else { <p class="empty-value">Keine.</p> }
            </div>
            <div>
              <h4>Children</h4>
              @if (neighborhood?.children?.length) {
                <ul data-relations="children">
                  @for (relation of neighborhood!.children; track relation.edge_id) {
                    <li [attr.data-edge-id]="relation.edge_id">
                      <strong>{{ relation.peer_label }}</strong>
                      <span>{{ relation.peer_role }}</span>
                      <code>{{ relation.edge_id }}</code>
                    </li>
                  }
                </ul>
              } @else { <p class="empty-value">Keine.</p> }
            </div>
            <div>
              <h4>Loops</h4>
              @if (neighborhood?.loops?.length) {
                <ul data-relations="loops">
                  @for (relation of neighborhood!.loops; track relation.edge_id) {
                    <li [attr.data-edge-id]="relation.edge_id">
                      <span>{{ relation.label || 'Loop' }}</span>
                      <code>{{ relation.edge_id }}</code>
                    </li>
                  }
                </ul>
              } @else { <p class="empty-value">Keine.</p> }
            </div>
          </div>
        </section>

        <details class="inspector-section advanced" data-section="advanced">
          <summary>Advanced: native Prozessdetails</summary>
          <dl>
            <div><dt>Step-ID</dt><dd><code>{{ selectedStep.id }}</code></dd></div>
            <div><dt>Task-Kind</dt><dd>{{ selectedStep.kind }}</dd></div>
            <div><dt>Position</dt><dd>{{ selectedStep.position.x }}, {{ selectedStep.position.y }}</dd></div>
            <div><dt>Human Gate</dt><dd>{{ selectedStep.gate ? 'Aktiv' : 'Nicht aktiv' }}</dd></div>
            <div>
              <dt>Policy-Hinweise</dt>
              <dd>{{ selectedStep.policy_hints.length ? selectedStep.policy_hints.join(', ') : 'Keine' }}</dd>
            </div>
          </dl>
        </details>

        @if (validationErrors.length) {
          <div class="validation-errors" role="alert" aria-label="Konfigurationsfehler">
            <p>Speichern ist nicht möglich:</p>
            <ul>
              @for (error of validationErrors; track error) { <li>{{ error }}</li> }
            </ul>
          </div>
        }

        <footer class="inspector-actions">
          <button
            type="button"
            class="primary"
            data-action="save"
            [disabled]="!canSave"
            (click)="save()"
          >Agent-Konfiguration speichern</button>
        </footer>
      }
    </aside>
  `,
  styleUrl: './caseflow-agent-node-inspector.component.scss',
})
export class CaseFlowAgentNodeInspectorComponent implements OnChanges {
  @Input({ required: true }) graph!: VpGraph;
  @Input({ required: true }) selectedStepId = '';
  @Input() catalogReadModel: CaseFlowAgentBindingCatalogReadModel | null = null;
  @Output() readonly graphChange = new EventEmitter<VpGraph>();

  readonly icons = CASEFLOW_AGENT_ICON_ALLOWLIST;
  readonly roleCategories = CASEFLOW_AGENT_ROLE_CATEGORIES;
  readonly personalityResourceTypes = PERSONALITY_RESOURCE_TYPES;
  readonly contextResourceTypes = CONTEXT_RESOURCE_TYPES;

  readonly controlPrefix = `caseflow-agent-inspector-${++inspectorSequence}`;
  readonly titleId = `${this.controlPrefix}-title`;
  readonly roleControlId = `${this.controlPrefix}-role`;
  readonly customRoleControlId = `${this.controlPrefix}-custom-role`;
  readonly iconControlId = `${this.controlPrefix}-icon`;
  readonly skillControlId = `${this.controlPrefix}-skill`;
  readonly skillStatusId = `${this.controlPrefix}-skill-status`;
  readonly personalityTypeControlId = `${this.controlPrefix}-personality-type`;
  readonly personalityIdControlId = `${this.controlPrefix}-personality-id`;
  readonly contextTypeControlId = `${this.controlPrefix}-context-type`;
  readonly contextIdControlId = `${this.controlPrefix}-context-id`;
  readonly modelRoleControlId = `${this.controlPrefix}-model-role`;
  readonly modelProfileControlId = `${this.controlPrefix}-model-profile`;
  readonly fallbackControlId = `${this.controlPrefix}-fallback`;

  selectedStep: Readonly<VpStep> | null = null;
  neighborhood: CaseFlowAgentNeighborhood | null = null;
  draft: CaseFlowAgentInspectorDraft | null = null;
  selectionError = '';
  validationErrors: readonly string[] = [];

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['graph'] || changes['selectedStepId']) {
      this.loadSelection();
      return;
    }
    if (changes['catalogReadModel']) this.validateDraft();
  }

  get canSave(): boolean {
    return Boolean(
      this.selectedStep
      && this.draft
      && this.catalogReadModel
      && this.validationErrors.length === 0,
    );
  }

  get skillProfileOptions(): readonly string[] {
    return this.optionsFor('skill_profile');
  }

  get modelProfileOptions(): readonly string[] {
    return this.optionsFor('model_profile');
  }

  get modelRoleOptions(): readonly string[] {
    return this.optionsFor('model_role');
  }

  get fallbackGroupOptions(): readonly string[] {
    return this.optionsFor('fallback_group');
  }

  get personalityOptions(): readonly string[] {
    if (!this.draft?.personalityType) return [];
    return this.optionsFor(this.draft.personalityType);
  }

  get personalitySelectable(): boolean {
    const type = this.draft?.personalityType;
    return Boolean(type && this.resourceSelectable(type));
  }

  get pendingContextOptions(): readonly string[] {
    if (!this.draft?.pendingContextType) return [];
    return this.optionsFor(this.draft.pendingContextType);
  }

  get pendingContextSelectable(): boolean {
    const type = this.draft?.pendingContextType;
    return Boolean(type && this.resourceSelectable(type));
  }

  get canAddContext(): boolean {
    if (!this.draft?.pendingContextType || !this.draft.pendingContextId) return false;
    return this.pendingContextSelectable
      && this.pendingContextOptions.includes(this.draft.pendingContextId)
      && !this.draft.contextBindings.some(binding =>
        binding.resource_type === this.draft!.pendingContextType
        && binding.resource_id === this.draft!.pendingContextId);
  }

  rolesFor(category: CaseFlowAgentRoleCategory) {
    return CASEFLOW_AGENT_ROLES_BY_CATEGORY[category];
  }

  roleCategoryLabel(category: CaseFlowAgentRoleCategory): string {
    const labels: Readonly<Record<CaseFlowAgentRoleCategory, string>> = {
      software: 'Software',
      creative: 'Kreativ',
      business: 'Business',
      generic: 'Allgemein',
    };
    return labels[category];
  }

  resourceLabel(kind: CaseFlowBindingResourceKind): string {
    return RESOURCE_LABELS[kind];
  }

  resourceState(kind: CaseFlowBindingResourceKind): string {
    return this.catalogReadModel?.availability[kind].state ?? 'degraded';
  }

  resourceReady(kind: CaseFlowBindingResourceKind): boolean {
    return this.catalogReadModel?.availability[kind].state === 'ready';
  }

  resourceSelectable(kind: CaseFlowBindingResourceKind): boolean {
    return this.resourceReady(kind) && this.optionsFor(kind).length > 0;
  }

  resourceStatus(kind: CaseFlowBindingResourceKind): string {
    const label = this.resourceLabel(kind);
    const availability = this.catalogReadModel?.availability[kind];
    if (!availability) return `${label}: nicht verfügbar – kein autorisierter Hub-Katalog.`;
    if (availability.state === 'degraded') {
      return `${label}: nicht verfügbar – Katalog konnte nicht sicher geladen werden (${availability.reason_code}).`;
    }
    if (availability.state === 'disabled') {
      return `${label}: nicht verfügbar – Katalog ist deaktiviert (${availability.reason_code}).`;
    }
    const count = this.optionsFor(kind).length;
    return count
      ? `${label}: ${count} autorisierte ${count === 1 ? 'Option' : 'Optionen'}.`
      : `${label}: verfügbar, aber ohne autorisierte Optionen.`;
  }

  bindingKey(binding: CaseFlowContextBindingRef): string {
    return `${binding.resource_type}:${binding.resource_id}`;
  }

  changeRole(event: Event): void {
    const roleId = eventValue(event);
    if (!this.draft || !isCaseFlowAgentRoleId(roleId)) return;
    const definition = roleDefinitionFor(roleId);
    this.draft = {
      ...this.draft,
      roleId,
      icon: definition.default_icon,
      ...(roleId === 'custom' ? {} : { customRoleName: '' }),
    };
    this.validateDraft();
  }

  changeCustomRoleName(event: Event): void {
    this.updateDraft({ customRoleName: eventValue(event) });
  }

  changeIcon(event: Event): void {
    const icon = eventValue(event);
    if (!isAllowedCaseFlowAgentIcon(icon)) return;
    this.updateDraft({ icon });
  }

  changeSkillProfile(event: Event): void {
    this.updateDraft({ skillProfileId: eventValue(event) });
  }

  clearSkillProfile(): void {
    this.updateDraft({ skillProfileId: '' });
  }

  changePersonalityType(event: Event): void {
    const resourceType = eventValue(event);
    if (!this.draft || !isPersonalityType(resourceType)) return;
    this.draft = {
      ...this.draft,
      personalityType: resourceType,
      personalityId: resourceType === this.draft.personalityType
        ? this.draft.personalityId
        : '',
    };
    this.validateDraft();
  }

  changePersonalityId(event: Event): void {
    this.updateDraft({ personalityId: eventValue(event) });
  }

  clearPersonality(): void {
    this.updateDraft({ personalityType: '', personalityId: '' });
  }

  changePendingContextType(event: Event): void {
    const resourceType = eventValue(event);
    if (!this.draft || !isContextType(resourceType)) return;
    this.draft = {
      ...this.draft,
      pendingContextType: resourceType,
      pendingContextId: resourceType === this.draft.pendingContextType
        ? this.draft.pendingContextId
        : '',
    };
    this.validateDraft();
  }

  changePendingContextId(event: Event): void {
    this.updateDraft({ pendingContextId: eventValue(event) });
  }

  addContextBinding(): void {
    if (!this.draft?.pendingContextType || !this.canAddContext) return;
    this.draft = {
      ...this.draft,
      contextBindings: [
        ...this.draft.contextBindings,
        {
          resource_type: this.draft.pendingContextType,
          resource_id: this.draft.pendingContextId,
        },
      ],
      pendingContextId: '',
    };
    this.validateDraft();
  }

  removeContextBinding(index: number): void {
    if (!this.draft || index < 0 || index >= this.draft.contextBindings.length) return;
    this.draft = {
      ...this.draft,
      contextBindings: this.draft.contextBindings.filter((_, itemIndex) => itemIndex !== index),
    };
    this.validateDraft();
  }

  changeModelRole(event: Event): void {
    this.updateDraft({ modelRole: eventValue(event) });
  }

  clearModelRole(): void {
    this.updateDraft({ modelRole: '' });
  }

  changeModelProfile(event: Event): void {
    this.updateDraft({ modelProfileId: eventValue(event) });
  }

  clearModelProfile(): void {
    this.updateDraft({ modelProfileId: '' });
  }

  changeFallbackGroup(event: Event): void {
    this.updateDraft({ fallbackGroupId: eventValue(event) });
  }

  clearFallbackGroup(): void {
    this.updateDraft({ fallbackGroupId: '' });
  }

  save(): void {
    this.validateDraft();
    if (!this.canSave || !this.draft || !this.catalogReadModel) return;

    const roleResult = setAgentRoleAndIcon(
      this.graph,
      this.selectedStepId,
      this.roleSelection(this.draft),
    );
    if (!roleResult.ok) {
      this.validationErrors = roleResult.issues.map(issue => issue.message);
      return;
    }

    const bindingResult = setAgentBindings(
      roleResult.value,
      this.selectedStepId,
      this.bindingDraft(this.draft),
      this.effectiveCatalog(),
    );
    if (!bindingResult.ok) {
      this.validationErrors = bindingResult.issues.map(issue => issue.message);
      return;
    }
    this.graphChange.emit(bindingResult.value);
  }

  private loadSelection(): void {
    this.selectedStep = null;
    this.neighborhood = null;
    this.draft = null;
    this.selectionError = '';
    this.validationErrors = [];
    if (!this.graph || !this.selectedStepId) return;

    const projection = projectAgentCanvas(this.graph);
    if (!projection.ok) {
      this.selectionError = projection.issues.map(issue => issue.message).join(' ');
      return;
    }
    const neighborhood = selectCaseFlowAgentNeighborhood(this.graph, this.selectedStepId);
    if (!neighborhood.ok) {
      this.selectionError = neighborhood.issues.map(issue => issue.message).join(' ');
      return;
    }

    const step = this.graph.steps.find(candidate => candidate.id === this.selectedStepId);
    const node = projection.value.nodes.find(candidate => candidate.step_id === this.selectedStepId);
    if (!step || !node) {
      this.selectionError = `Agent step "${this.selectedStepId}" was not found.`;
      return;
    }

    this.selectedStep = step;
    this.neighborhood = neighborhood.value;
    this.draft = {
      roleId: node.role_preset,
      customRoleName: node.role_preset === 'custom' ? node.role : '',
      icon: node.icon,
      skillProfileId: node.configuration.skill_profile_id ?? '',
      personalityType: node.configuration.personality_binding?.resource_type ?? '',
      personalityId: node.configuration.personality_binding?.resource_id ?? '',
      contextBindings: node.configuration.context_bindings.map(binding => ({ ...binding })),
      pendingContextType: '',
      pendingContextId: '',
      modelRole: node.configuration.model_routing.model_role ?? '',
      modelProfileId: node.configuration.model_routing.preferred_profile_id ?? '',
      fallbackGroupId: node.configuration.model_routing.fallback_group_id ?? '',
    };
    this.validateDraft();
  }

  private updateDraft(update: Partial<CaseFlowAgentInspectorDraft>): void {
    if (!this.draft) return;
    this.draft = { ...this.draft, ...update };
    this.validateDraft();
  }

  private validateDraft(): void {
    if (!this.draft) {
      this.validationErrors = [];
      return;
    }
    const errors: string[] = [];
    const role = validateRoleSelection(this.roleSelection(this.draft));
    if (!role.ok) errors.push(...role.issues.map(issue => issue.message));

    if (!this.catalogReadModel) {
      errors.push('Der autorisierte Hub-Katalog ist noch nicht verfügbar.');
    } else {
      if (this.draft.personalityType && !this.draft.personalityId) {
        errors.push(`${this.resourceLabel(this.draft.personalityType)} benötigt eine autorisierte Ressourcen-ID.`);
      }
      errors.push(...validateBindingDraft(
        this.bindingDraft(this.draft),
        this.effectiveCatalog(),
      ).map(issue => issue.message));
    }
    this.validationErrors = Array.from(new Set(errors));
  }

  private roleSelection(draft: CaseFlowAgentInspectorDraft): CaseFlowAgentRoleSelection {
    if (draft.roleId === 'custom') {
      return {
        role_id: 'custom',
        custom_name: draft.customRoleName,
        icon: draft.icon,
      };
    }
    return { role_id: draft.roleId, icon: draft.icon };
  }

  private bindingDraft(draft: CaseFlowAgentInspectorDraft): CaseFlowAgentBindingDraft {
    return {
      skill_profile_id: draft.skillProfileId || null,
      personality_binding: draft.personalityType && draft.personalityId
        ? {
          resource_type: draft.personalityType,
          resource_id: draft.personalityId,
        }
        : null,
      context_bindings: draft.contextBindings,
      model_routing: {
        model_role: draft.modelRole || null,
        preferred_profile_id: draft.modelProfileId || null,
        fallback_group_id: draft.fallbackGroupId || null,
      },
    };
  }

  private optionsFor(kind: CaseFlowBindingResourceKind): readonly string[] {
    if (!this.resourceReady(kind)) return [];
    const catalog = this.catalogReadModel?.catalog;
    if (!catalog) return [];
    switch (kind) {
      case 'skill_profile': return catalog.skill_profile_ids;
      case 'agent_profile': return catalog.personality_resource_ids.agent_profile;
      case 'instruction_layer': return catalog.personality_resource_ids.instruction_layer;
      case 'context_profile': return catalog.context_resource_ids.context_profile;
      case 'context_source': return catalog.context_resource_ids.context_source;
      case 'model_profile': return catalog.model_profile_ids;
      case 'model_role': return catalog.model_role_ids;
      case 'fallback_group': return catalog.fallback_group_ids;
    }
  }

  /** Defense in depth: availability, not a possibly stale payload, grants use. */
  private effectiveCatalog(): CaseFlowAgentBindingCatalog {
    return {
      skill_profile_ids: this.optionsFor('skill_profile'),
      personality_resource_ids: {
        agent_profile: this.optionsFor('agent_profile'),
        instruction_layer: this.optionsFor('instruction_layer'),
      },
      context_resource_ids: {
        context_profile: this.optionsFor('context_profile'),
        context_source: this.optionsFor('context_source'),
      },
      model_profile_ids: this.optionsFor('model_profile'),
      model_role_ids: this.optionsFor('model_role'),
      fallback_group_ids: this.optionsFor('fallback_group'),
    };
  }
}

function eventValue(event: Event): string {
  const target = event.target;
  return target instanceof HTMLInputElement || target instanceof HTMLSelectElement
    ? target.value
    : '';
}

function isPersonalityType(value: string): value is OptionalPersonalityType {
  return value === '' || PERSONALITY_RESOURCE_TYPES.includes(
    value as CaseFlowPersonalityResourceType,
  );
}

function isContextType(value: string): value is OptionalContextType {
  return value === '' || CONTEXT_RESOURCE_TYPES.includes(
    value as CaseFlowContextResourceType,
  );
}
