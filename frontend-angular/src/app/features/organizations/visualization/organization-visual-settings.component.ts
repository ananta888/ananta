import { ChangeDetectionStrategy, Component, inject, Input } from '@angular/core';

import { OrganizationEdgeKind, OrganizationEdgeNamespace, OrganizationNodeKind } from '../models/organization-topology.models';
import { OrganizationRoleVisualTarget } from './organization-graph-projection.service';
import {
  DEFAULT_ORGANIZATION_EDGE_KIND_COLORS,
  ORGANIZATION_LEADERSHIP_LABELS,
  ORGANIZATION_NODE_KIND_LABELS,
  OrganizationLeadershipScope,
  OrganizationNodeColorMetric,
  OrganizationNodeSizeMetric,
  OrganizationEdgeColorMetric,
  OrganizationEdgeStrengthMetric,
} from './organization-visual-profile.models';
import { OrganizationVisualProfileService } from './organization-visual-profile.service';

@Component({
  selector: 'app-organization-visual-settings',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <details class="settings">
      <summary>3D-Darstellung einstellen</summary>
      <p class="notice">
        Visuelle Gewichtung ändert keine Rechte, Priorität oder Hub-Routing. Führungsebenen und Wichtigkeit
        werden ausschließlich explizit gesetzt und niemals aus Rollennamen abgeleitet.
      </p>

      <div class="settings-grid">
        <label>Knotengröße
          <select [value]="profile.profile().nodeSizeMetric" (change)="setNodeSizeMetric($event)">
            <option value="kind">Knotentyp</option>
            <option value="importance">Explizite Wichtigkeit</option>
            <option value="leadership_scope">Explizite Führungsebene</option>
            <option value="runtime_load">Runtime-Auslastung</option>
            <option value="degree">Verbindungsgrad</option>
          </select>
        </label>
        <label>Knotenfarbe
          <select [value]="profile.profile().nodeColorMetric" (change)="setNodeColorMetric($event)">
            <option value="kind">Knotentyp</option>
            <option value="importance">Explizite Wichtigkeit</option>
            <option value="leadership_scope">Explizite Führungsebene</option>
            <option value="runtime_state">Runtime-Status</option>
          </select>
        </label>
        <label>Kantenfarbe
          <select [value]="profile.profile().edgeColorMetric" (change)="setEdgeColorMetric($event)">
            <option value="namespace">Namespace</option>
            <option value="kind">Kantenart</option>
          </select>
        </label>
        <label>Kantenstärke
          <select [value]="profile.profile().edgeStrengthMetric" (change)="setEdgeStrengthMetric($event)">
            <option value="fixed">Einheitlich</option>
            <option value="kind_weight">Explizites Gewicht je Kantenart</option>
            <option value="runtime_provenance">Runtime-Provenienz</option>
          </select>
        </label>
      </div>

      <fieldset>
        <legend>Darstellungsbereiche</legend>
        <div class="settings-grid">
          <label>Knotengröße min.
            <input type="number" min="1" max="100" [value]="profile.profile().nodeSizeRange.min" (change)="setNodeRange('min', $event)" />
          </label>
          <label>Knotengröße max.
            <input type="number" min="1" max="100" [value]="profile.profile().nodeSizeRange.max" (change)="setNodeRange('max', $event)" />
          </label>
          <label>Kantenstärke min.
            <input type="number" min="0.1" max="20" step="0.1" [value]="profile.profile().edgeWidthRange.min" (change)="setEdgeRange('min', $event)" />
          </label>
          <label>Kantenstärke max.
            <input type="number" min="0.1" max="20" step="0.1" [value]="profile.profile().edgeWidthRange.max" (change)="setEdgeRange('max', $event)" />
          </label>
        </div>
      </fieldset>

      <fieldset>
        <legend>Farben der Knotentypen</legend>
        <div class="color-grid">
          @for (kind of nodeKinds; track kind) {
            <label>{{ nodeKindLabels[kind] }}
              <input type="color" [value]="profile.profile().nodeKindColors[kind]" (change)="setNodeKindColor(kind, $event)" />
            </label>
          }
        </div>
      </fieldset>

      <fieldset>
        <legend>Farben der Kanten-Namespaces</legend>
        <div class="color-grid">
          @for (namespace of edgeNamespaces; track namespace) {
            <label>{{ namespace }}
              <input type="color" [value]="profile.profile().edgeNamespaceColors[namespace]" (change)="setEdgeNamespaceColor(namespace, $event)" />
            </label>
          }
        </div>
      </fieldset>

      @if (edgeKinds.length) {
        <fieldset>
          <legend>Kantenarten</legend>
          <div class="edge-grid">
            @for (kind of edgeKinds; track kind) {
              <label>{{ kind }}
                <input type="color" [value]="edgeKindColor(kind)" (change)="setEdgeKindColor(kind, $event)" />
              </label>
              <label>Gewicht
                <input type="number" min="0" max="1" step="0.05" [value]="edgeKindWeight(kind)" (change)="setEdgeKindWeight(kind, $event)" />
              </label>
            }
          </div>
        </fieldset>
      }

      @if (roleTargets.length) {
        <fieldset>
          <legend>Explizite Rollen-Gewichtung</legend>
          <p class="hint">Overrides gelten für den stabilen Slot-Schlüssel. Der Template-Verweis dient nur als Information.</p>
          <div class="roles">
            @for (role of roleTargets; track role.key) {
              <article>
                <header>
                  <strong>{{ role.label }}</strong>
                  <small>Team: {{ role.teamLabel || 'außerhalb des geladenen Ausschnitts' }}</small>
                  @if (role.roleTemplateRef) { <small>Template: {{ role.roleTemplateRef }}</small> }
                  <code>{{ role.key }}</code>
                </header>
                <label>Wichtigkeit
                  <input type="number" min="0" max="100" [value]="roleImportance(role.key)" (change)="setRoleImportance(role.key, $event)" />
                </label>
                <label>Führungsebene
                  <select [value]="roleLeadership(role.key)" (change)="setRoleLeadership(role.key, $event)">
                    @for (scope of leadershipScopes; track scope) {
                      <option [value]="scope">{{ leadershipLabels[scope] }}</option>
                    }
                  </select>
                </label>
                <label>Farbe
                  <input type="color" [value]="roleColor(role.key)" (change)="setRoleColor(role.key, $event)" />
                </label>
                <button type="button" (click)="profile.clearRoleOverride(role.key)" [disabled]="!hasRoleOverride(role.key)">Override löschen</button>
              </article>
            }
          </div>
        </fieldset>
      }

      <button type="button" (click)="profile.reset()">Darstellung zurücksetzen</button>
    </details>
  `,
  styles: [`
    .settings { background: #0d1728; border: 1px solid #334a6d; border-radius: .55rem; padding: .55rem .7rem; }
    summary { color: #dce8fa; cursor: pointer; font-weight: 700; }
    .notice { background: #18253a; border-left: 4px solid #60a5fa; color: #bfcee5; font-size: .78rem; padding: .55rem; }
    fieldset { border: 1px solid #304665; border-radius: .45rem; margin: .7rem 0; padding: .6rem; }
    legend { color: #9fbcdf; font-size: .78rem; font-weight: 700; }
    label { color: #bac9df; display: grid; font-size: .72rem; gap: .2rem; }
    select, input, button { background: #101c30; border: 1px solid #405a7e; border-radius: .35rem; color: #eef5ff; font: inherit; padding: .35rem; }
    button { cursor: pointer; } button:disabled { cursor: default; opacity: .45; }
    input[type='color'] { min-height: 2rem; padding: .15rem; width: 3.2rem; }
    .settings-grid, .color-grid { display: grid; gap: .55rem; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); margin-top: .65rem; }
    .color-grid label { align-items: center; display: flex; justify-content: space-between; }
    .edge-grid { display: grid; gap: .45rem; grid-template-columns: minmax(150px, 1fr) minmax(100px, .5fr); }
    .edge-grid label { align-items: center; display: grid; grid-template-columns: 1fr auto; }
    .roles { display: grid; gap: .45rem; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
    .roles article { background: #111f34; border: 1px solid #304665; border-radius: .4rem; display: grid; gap: .45rem; padding: .5rem; }
    .roles header { display: grid; } small, code, .hint { color: #8fa4c2; font-size: .68rem; overflow-wrap: anywhere; }
    :focus-visible { outline: 3px solid #7eb2f5; outline-offset: 2px; }
  `],
})
export class OrganizationVisualSettingsComponent {
  readonly profile = inject(OrganizationVisualProfileService);
  readonly nodeKinds: readonly OrganizationNodeKind[] = [
    'organization', 'coordination_unit', 'value_stream', 'team', 'role_slot', 'assignment',
  ];
  readonly edgeNamespaces: readonly OrganizationEdgeNamespace[] = ['hierarchy', 'organization', 'runtime'];
  readonly leadershipScopes: readonly OrganizationLeadershipScope[] = ['none', 'team', 'multi_team', 'organization'];
  readonly nodeKindLabels = ORGANIZATION_NODE_KIND_LABELS;
  readonly leadershipLabels = ORGANIZATION_LEADERSHIP_LABELS;

  @Input() roleTargets: readonly OrganizationRoleVisualTarget[] = [];
  @Input() edgeKinds: readonly OrganizationEdgeKind[] = [];

  setNodeSizeMetric(event: Event): void {
    this.profile.setNodeSizeMetric((event.target as HTMLSelectElement).value as OrganizationNodeSizeMetric);
  }

  setNodeColorMetric(event: Event): void {
    this.profile.setNodeColorMetric((event.target as HTMLSelectElement).value as OrganizationNodeColorMetric);
  }

  setEdgeColorMetric(event: Event): void {
    this.profile.setEdgeColorMetric((event.target as HTMLSelectElement).value as OrganizationEdgeColorMetric);
  }

  setEdgeStrengthMetric(event: Event): void {
    this.profile.setEdgeStrengthMetric((event.target as HTMLSelectElement).value as OrganizationEdgeStrengthMetric);
  }

  setNodeRange(bound: 'min' | 'max', event: Event): void {
    this.profile.setNodeRange(bound, numberValue(event));
  }

  setEdgeRange(bound: 'min' | 'max', event: Event): void {
    this.profile.setEdgeRange(bound, numberValue(event));
  }

  setNodeKindColor(kind: OrganizationNodeKind, event: Event): void {
    this.profile.setNodeKindColor(kind, value(event));
  }

  setEdgeNamespaceColor(namespace: OrganizationEdgeNamespace, event: Event): void {
    this.profile.setEdgeNamespaceColor(namespace, value(event));
  }

  setEdgeKindColor(kind: OrganizationEdgeKind, event: Event): void {
    this.profile.setEdgeKindColor(kind, value(event));
  }

  setEdgeKindWeight(kind: OrganizationEdgeKind, event: Event): void {
    this.profile.setEdgeKindWeight(kind, numberValue(event));
  }

  setRoleImportance(key: string, event: Event): void {
    this.profile.setRoleOverride(key, { importance: numberValue(event) });
  }

  setRoleLeadership(key: string, event: Event): void {
    this.profile.setRoleOverride(key, {
      leadershipScope: value(event) as OrganizationLeadershipScope,
    });
  }

  setRoleColor(key: string, event: Event): void {
    this.profile.setRoleOverride(key, { color: value(event) });
  }

  roleImportance(key: string): number {
    return this.profile.roleOverride(key)?.importance ?? 50;
  }

  roleLeadership(key: string): OrganizationLeadershipScope {
    return this.profile.roleOverride(key)?.leadershipScope ?? 'none';
  }

  roleColor(key: string): string {
    return this.profile.roleOverride(key)?.color
      ?? this.profile.profile().nodeKindColors.role_slot;
  }

  edgeKindColor(kind: OrganizationEdgeKind): string {
    return this.profile.profile().edgeKindColors[kind]
      ?? DEFAULT_ORGANIZATION_EDGE_KIND_COLORS[kind];
  }

  edgeKindWeight(kind: OrganizationEdgeKind): number {
    return this.profile.profile().edgeKindWeights[kind] ?? 0.4;
  }

  hasRoleOverride(key: string): boolean {
    return Boolean(this.profile.roleOverride(key));
  }
}

function value(event: Event): string {
  return (event.target as HTMLInputElement | HTMLSelectElement).value;
}

function numberValue(event: Event): number {
  return Number(value(event));
}
