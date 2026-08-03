import { CommonModule } from '@angular/common';
import { Component, computed, effect, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { OrganizationPatchOperation } from '../models/organization-topology.models';
import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';

@Component({
  selector: 'app-organization-topology-editor',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <section class="editor" aria-labelledby="topology-editor-heading">
      <header>
        <div>
          <p class="eyebrow">Hub-validierter Draft</p>
          <h2 id="topology-editor-heading">Topologie ändern</h2>
        </div>
        <span>Revision <code>{{ state.topology()?.definition_revision || '–' }}</code></span>
      </header>

      <div class="builder">
        <label>
          Operation
          <select [(ngModel)]="operationKind" (ngModelChange)="resetPreview()">
            <option value="add">Add</option>
            <option value="remove">Remove</option>
            <option value="reparent">Reparent</option>
            <option value="connect">Connect</option>
            <option value="assign">Assign</option>
          </select>
        </label>

        @if (operationKind === 'add') {
          <label>Knotentyp
            <select [(ngModel)]="nodeKind">
              <option value="coordination_unit">Coordination Unit</option>
              <option value="value_stream">Value Stream</option>
              <option value="team">Team</option>
              <option value="role_slot">Role Slot</option>
            </select>
          </label>
          <label>Parent-ID <input [(ngModel)]="parentId" /></label>
          <label>Stabiler Schlüssel <input [(ngModel)]="stableKey" /></label>
          <label>Bezeichnung <input [(ngModel)]="label" /></label>
          @if (nodeKind === 'team') {
            <label>Team-Blueprint-Ref <input [(ngModel)]="teamBlueprintRef" placeholder="key@version" /></label>
          }
          @if (nodeKind === 'role_slot') {
            <label>Role-Template-Ref <input [(ngModel)]="roleTemplateRef" placeholder="key@version" /></label>
            <label>Minimum <input type="number" min="0" [(ngModel)]="minCount" /></label>
            <label>Default <input type="number" min="0" [(ngModel)]="defaultCount" /></label>
            <label>Maximum <input type="number" min="1" [(ngModel)]="maxCount" /></label>
            <label>Pflichtrolle <input type="checkbox" [(ngModel)]="requiredRole" /></label>
            <label>Benötigte Capabilities <input [(ngModel)]="requiredCapabilities" placeholder="code_edit, review" /></label>
            <label>Verbotene Capabilities <input [(ngModel)]="forbiddenCapabilities" /></label>
            <label>SoD-Enforcement
              <select [(ngModel)]="sodEnforcement">
                <option value="none">None</option>
                <option value="warn">Warn</option>
                <option value="strict">Strict</option>
              </select>
            </label>
            <label>Unabhängig von Slots <input [(ngModel)]="independentSlotIds" /></label>
          }
        }

        @if (operationKind === 'remove') {
          <label>Knoten-ID <input [(ngModel)]="nodeId" /></label>
          <label>Lifecycle-Strategie
            <select [(ngModel)]="lifecycleStrategy">
              <option value="drain">Drain</option>
              <option value="migrate">Migrate</option>
              <option value="archive">Archive</option>
            </select>
          </label>
          @if (lifecycleStrategy === 'migrate') {
            <label>Ziel-Organization <input [(ngModel)]="migrationOrganizationId" /></label>
            <label>Ziel-Team-Unit <input [(ngModel)]="migrationUnitId" /></label>
            <label>Ziel-Team <input [(ngModel)]="migrationTeamId" /></label>
            <label>Ziel-Role-Slot <input [(ngModel)]="migrationRoleSlotId" /></label>
            <p class="note">Migration erzeugt Hub-seitig lineage-gebundene Successor-Tasks.</p>
          }
        }

        @if (operationKind === 'reparent') {
          <label>Knoten-ID <input [(ngModel)]="nodeId" /></label>
          <label>Neue Parent-ID <input [(ngModel)]="parentId" /></label>
          <label>Aktive Arbeit
            <select [(ngModel)]="activeStrategy">
              <option value="drain">Drain</option>
              <option value="migrate">Migrate</option>
            </select>
          </label>
        }

        @if (operationKind === 'connect') {
          <label>Kantentyp
            <select [(ngModel)]="edgeKind">
              <option value="declared_dependency">Declared Dependency</option>
              <option value="handoff">Handoff</option>
            </select>
          </label>
          <label>Source-ID <input [(ngModel)]="sourceId" /></label>
          <label>Target-ID <input [(ngModel)]="targetId" /></label>
          <p class="note">Runtime-Kanten sind schreibgeschützt und können hier nicht ausgewählt werden.</p>
        }

        @if (operationKind === 'assign') {
          <label>Role-Slot-ID <input [(ngModel)]="roleSlotId" /></label>
          <label>Agent-ID <input [(ngModel)]="agentId" /></label>
          <p class="note">Capability, Kapazität und Separation of Duties werden vom Hub erneut geprüft.</p>
        }

        <button type="button" (click)="preview()" [disabled]="state.mutating() || !canPreview()">Draft prüfen</button>
      </div>

      @if (state.patchPreview(); as preview) {
        <section class="preview" aria-labelledby="patch-preview-heading">
          <h3 id="patch-preview-heading">Dry-run-Ergebnis</h3>
          <p><strong>{{ preview.applicable ? 'anwendbar' : 'blockiert' }}</strong> · {{ preview.operations.length }} Operation(en)</p>
          <ul>
            @for (write of preview.planned_writes; track write) { <li>{{ write }}</li> }
          </ul>
          @if (preview.diagnostics.length) {
            <ul class="diagnostics">
              @for (diagnostic of preview.diagnostics; track diagnostic.reason_code + diagnostic.message) {
                <li [attr.data-severity]="diagnostic.severity"><strong>{{ diagnostic.severity }}</strong> · {{ diagnostic.message }} <small>{{ diagnostic.reason_code }}</small></li>
              }
            </ul>
          }
          <p class="digest"><strong>Patch-Digest:</strong> <code>{{ preview.patch_digest }}</code></p>
          <label>Parent Organization-Admin-Grant <input type="password" [(ngModel)]="adminGrant" autocomplete="off" /></label>
          <label class="confirm"><input type="checkbox" [(ngModel)]="confirmed" /> Dry-run und Lifecycle-Auswirkung bewusst bestätigen</label>
          @if (state.topologyPatchGrant(); as grant) {
            <p><strong>One-shot Grant gebunden</strong> · gültig bis {{ grant.expires_at }}</p>
          }
          <div class="actions">
            <button type="button" class="secondary" (click)="resetPreview()">Verwerfen</button>
            <button type="button" class="secondary" (click)="issueGrant()" [disabled]="!preview.applicable || !confirmed || !adminGrant.trim() || state.mutating()">One-shot Grant binden</button>
            <button type="button" (click)="apply()" [disabled]="!preview.applicable || !confirmed || !state.topologyPatchGrant() || state.mutating()">Exakt gebunden anwenden</button>
          </div>
        </section>
      }
    </section>
  `,
  styles: [`
    .editor { display: grid; gap: 1rem; max-width: 1050px; } header { align-items: end; display: flex; justify-content: space-between; }
    h2, h3, p { margin: 0; } .eyebrow { color: #76a9ff; font-size: .72rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
    .builder { align-items: end; background: #0d1728; border: 1px solid #304361; border-radius: .7rem; display: grid; gap: .7rem; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); padding: .85rem; }
    label { color: #c9d6eb; display: grid; font-size: .82rem; gap: .3rem; } input, select { background: #111d31; border: 1px solid #405677; border-radius: .4rem; color: white; padding: .55rem; }
    button { background: #2d73cd; border: 0; border-radius: .4rem; color: white; cursor: pointer; min-height: 2.35rem; padding: .5rem .75rem; } button.secondary { background: #263953; } button:disabled { opacity: .45; }
    .note { align-self: center; color: #9aabc6; font-size: .76rem; }
    .preview { background: #101c30; border: 1px solid #365075; border-radius: .7rem; display: grid; gap: .75rem; padding: 1rem; }
    .diagnostics { display: grid; gap: .35rem; list-style: none; padding: 0; } .diagnostics li { border-left: 3px solid #607ea9; background: #0b1423; padding: .5rem; }
    .diagnostics li[data-severity='blocker'] { border-color: #e66d78; } .diagnostics li[data-severity='warning'] { border-color: #dfa743; } small { color: #91a3c0; display: block; }
    .digest { overflow-wrap: anywhere; } .confirm { align-items: center; display: flex; flex-direction: row; }
    .actions { display: flex; gap: .5rem; justify-content: flex-end; }
  `],
})
export class OrganizationTopologyEditorComponent {
  readonly state = inject(OrganizationTopologyStateService);
  readonly selectedNodeId = computed(() => this.state.selectedNodeId() ?? '');

  operationKind: OrganizationPatchOperation['op'] = 'add';
  nodeKind: 'coordination_unit' | 'value_stream' | 'team' | 'role_slot' = 'team';
  nodeId = '';
  parentId = '';
  stableKey = '';
  label = '';
  teamBlueprintRef = '';
  roleTemplateRef = '';
  minCount = 1;
  defaultCount = 1;
  maxCount = 1;
  requiredRole = true;
  requiredCapabilities = '';
  forbiddenCapabilities = '';
  sodEnforcement: 'none' | 'warn' | 'strict' = 'strict';
  independentSlotIds = '';
  lifecycleStrategy: 'drain' | 'migrate' | 'archive' = 'drain';
  activeStrategy: 'drain' | 'migrate' = 'drain';
  migrationOrganizationId = '';
  migrationUnitId = '';
  migrationTeamId = '';
  migrationRoleSlotId = '';
  edgeKind: 'declared_dependency' | 'handoff' = 'declared_dependency';
  sourceId = '';
  targetId = '';
  roleSlotId = '';
  agentId = '';
  adminGrant = '';
  confirmed = false;
  private observedScope = this.scopeKey();

  constructor() {
    effect(() => {
      const scope = this.scopeKey();
      if (scope === this.observedScope) return;
      this.observedScope = scope;
      this.resetForScopeChange();
    });
  }

  canPreview(): boolean {
    if (this.operationKind === 'add') {
      const baseValid = Boolean(this.parentId.trim() && this.stableKey.trim() && this.label.trim());
      if (!baseValid) return false;
      if (this.nodeKind === 'team') return Boolean(this.teamBlueprintRef.trim());
      if (this.nodeKind === 'role_slot') {
        return Boolean(
          this.roleTemplateRef.trim()
          && this.minCount >= 0
          && this.defaultCount >= this.minCount
          && this.maxCount >= Math.max(1, this.defaultCount)
          && (!this.requiredRole || this.minCount >= 1)
        );
      }
      return true;
    }
    if (this.operationKind === 'remove') {
      const selected = Boolean(this.nodeId.trim() || this.selectedNodeId());
      if (this.lifecycleStrategy !== 'migrate') return selected;
      return selected && Boolean(
        this.migrationOrganizationId.trim()
        && this.migrationUnitId.trim()
        && this.migrationTeamId.trim()
        && this.migrationRoleSlotId.trim()
      );
    }
    if (this.operationKind === 'reparent') return Boolean((this.nodeId.trim() || this.selectedNodeId()) && this.parentId.trim());
    if (this.operationKind === 'connect') return Boolean(this.sourceId.trim() && this.targetId.trim());
    return Boolean(this.roleSlotId.trim() && this.agentId.trim());
  }

  preview(): void {
    const operation = this.buildOperation();
    if (!operation) return;
    this.confirmed = false;
    this.adminGrant = this.state.selectedOrganizationAdminGrant();
    this.state.previewOperations([operation]);
  }

  apply(): void {
    if (!this.confirmed) return;
    this.state.applyPreview();
  }

  issueGrant(): void {
    if (!this.confirmed) return;
    this.state.issuePreviewGrant(this.adminGrant);
  }

  resetPreview(): void {
    this.state.patchPreview.set(null);
    this.state.topologyPatchGrant.set(null);
    this.confirmed = false;
    this.adminGrant = '';
  }

  private scopeKey(): string {
    return `${this.state.projectId()}|${this.state.selectedOrganizationId() || ''}`;
  }

  private resetForScopeChange(): void {
    this.nodeId = '';
    this.parentId = '';
    this.stableKey = '';
    this.label = '';
    this.teamBlueprintRef = '';
    this.roleTemplateRef = '';
    this.independentSlotIds = '';
    this.migrationOrganizationId = '';
    this.migrationUnitId = '';
    this.migrationTeamId = '';
    this.migrationRoleSlotId = '';
    this.sourceId = '';
    this.targetId = '';
    this.roleSlotId = '';
    this.agentId = '';
    this.adminGrant = '';
    this.confirmed = false;
  }

  private buildOperation(): OrganizationPatchOperation | null {
    const selected = this.nodeId.trim() || this.selectedNodeId();
    switch (this.operationKind) {
      case 'add': return this.buildAddOperation();
      case 'remove': {
        if (!selected) return null;
        if (this.lifecycleStrategy === 'migrate') {
          return {
            op: 'remove',
            node_id: selected,
            lifecycle_strategy: 'migrate',
            migration_target: {
              organization_id: this.migrationOrganizationId.trim(),
              unit_id: this.migrationUnitId.trim(),
              team_id: this.migrationTeamId.trim(),
              role_slot_id: this.migrationRoleSlotId.trim(),
            },
          };
        }
        return { op: 'remove', node_id: selected, lifecycle_strategy: this.lifecycleStrategy };
      }
      case 'reparent': return selected ? {
        op: 'reparent', node_id: selected, parent_id: this.parentId.trim(), lifecycle_strategy: this.activeStrategy,
      } : null;
      case 'connect': return {
        op: 'connect', namespace: 'organization', edge_kind: this.edgeKind,
        source_id: this.sourceId.trim(), target_id: this.targetId.trim(),
      };
      case 'assign': return { op: 'assign', role_slot_id: this.roleSlotId.trim(), agent_id: this.agentId.trim() };
    }
  }

  private buildAddOperation(): Extract<OrganizationPatchOperation, { op: 'add' }> {
    const base = {
      stable_key: this.stableKey.trim(),
      name: this.label.trim(),
    };
    if (this.nodeKind === 'team') {
      return {
        op: 'add',
        node_kind: 'team',
        parent_id: this.parentId.trim(),
        value: { ...base, team_blueprint_ref: this.teamBlueprintRef.trim() },
      };
    }
    if (this.nodeKind === 'role_slot') {
      return {
        op: 'add',
        node_kind: 'role_slot',
        parent_id: this.parentId.trim(),
        value: {
          ...base,
          slot_key: this.stableKey.trim(),
          role_template_ref: this.roleTemplateRef.trim(),
          required: this.requiredRole,
          min_count: this.minCount,
          default_count: this.defaultCount,
          max_count: this.maxCount,
          assignment_policy: {
            principal_kinds: ['agent', 'human'],
            required_capabilities: commaSeparated(this.requiredCapabilities),
            forbidden_capabilities: commaSeparated(this.forbiddenCapabilities),
            write_access_required: false,
          },
          separation_of_duties: {
            enforcement: this.sodEnforcement,
            independent_from_slot_ids: commaSeparated(this.independentSlotIds),
            independent_from_external_duties: [],
          },
          overlays: [],
        },
      };
    }
    return {
      op: 'add',
      node_kind: this.nodeKind,
      parent_id: this.parentId.trim(),
      value: base,
    };
  }
}

function commaSeparated(value: string): readonly string[] {
  return [...new Set(value.split(',').map(item => item.trim()).filter(Boolean))];
}
