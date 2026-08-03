import { CommonModule } from '@angular/common';
import { Component, computed, ElementRef, inject, signal, viewChildren } from '@angular/core';

import { OrganizationTopologyNode } from '../../models/organization-topology.models';
import { OrganizationTopologyStateService } from '../../services/organization-topology-state.service';

@Component({
  selector: 'app-organization-hierarchy',
  standalone: true,
  imports: [CommonModule],
  template: `
    <section class="hierarchy" aria-labelledby="organization-hierarchy-heading">
      <header>
        <div>
          <p class="eyebrow">Lazy Read Model</p>
          <h2 id="organization-hierarchy-heading">Hierarchie</h2>
        </div>
        <span aria-live="polite">{{ state.statusText() }}</span>
      </header>

      <div
        class="tree"
        role="tree"
        aria-label="Organisation, Units, Teams, Rollen und Zuweisungen"
        [attr.aria-busy]="state.loading()">
        @for (node of rows(); track node.id; let index = $index) {
          <button
            #treeitem
            type="button"
            class="tree-row"
            role="treeitem"
            [attr.aria-level]="node.depth + 1"
            [attr.aria-expanded]="node.child_count ? !effectiveCollapsed().has(node.id) : null"
            [attr.aria-selected]="state.selectedNodeId() === node.id"
            [tabIndex]="state.selectedNodeId() === node.id || (!state.selectedNodeId() && index === 0) ? 0 : -1"
            [style.--tree-depth]="node.depth"
            (click)="select(node)"
            (keydown)="onKeydown($event, index, node)">
            <span class="disclosure" aria-hidden="true" (click)="toggle($event, node)">
              {{ node.child_count ? (effectiveCollapsed().has(node.id) ? '▸' : '▾') : '·' }}
            </span>
            <span class="kind">{{ kindLabel(node.kind) }}</span>
            <span class="label">{{ node.label }}</span>
            @if (node.child_count) { <span class="count" [attr.aria-label]="node.child_count + ' untergeordnete Elemente'">{{ node.child_count }}</span> }
            @if (runtimeLabel(node.id); as runtime) {
              <span class="status" [attr.data-state]="runtime.state">{{ runtime.label }}</span>
            } @else {
              <span class="status neutral">keine Laufzeitdaten</span>
            }
            @if (node.has_more_children) { <span class="more">weitere serverseitig</span> }
          </button>
        } @empty {
          <p class="empty">Für die aktuellen Filter wurden keine Knoten geladen.</p>
        }
      </div>

      <footer>
        @if (state.canLoadMore()) {
          <button type="button" (click)="state.loadMore()" [disabled]="state.loading()">Nächste Seite laden</button>
        }
        @if (state.topology()?.truncated) {
          <p>Die Ansicht ist durch das serverseitige Renderlimit begrenzt. Nutze Suche oder Fokus-Subgraph.</p>
        }
      </footer>
    </section>
  `,
  styles: [`
    .hierarchy { display: grid; gap: .75rem; min-width: 0; }
    header { display: flex; align-items: end; justify-content: space-between; gap: 1rem; }
    h2, p { margin: 0; } .eyebrow { color: #76a9ff; font-size: .72rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
    header > span { color: #9eafd0; font-size: .8rem; }
    .tree { background: #0b1424; border: 1px solid #2c3d5c; border-radius: .65rem; max-height: min(68vh, 860px); overflow: auto; padding: .4rem; }
    .tree-row { --tree-depth: 0; align-items: center; background: transparent; border: 1px solid transparent; border-radius: .4rem; color: #d9e4fa; cursor: pointer; display: grid; gap: .55rem; grid-template-columns: 1.2rem 7.5rem minmax(10rem, 1fr) auto auto auto; margin: .12rem 0; min-height: 2.55rem; padding: .4rem .6rem .4rem calc(.6rem + var(--tree-depth) * 1.25rem); text-align: left; width: 100%; }
    .tree-row:hover { background: #111e34; } .tree-row:focus-visible { outline: 3px solid #76a9ff; outline-offset: -2px; }
    .tree-row[aria-selected='true'] { background: #17345c; border-color: #4d8ad9; }
    .disclosure { color: #8facd8; font-size: 1.1rem; text-align: center; }
    .kind { color: #88a4cf; font-size: .72rem; letter-spacing: .04em; text-transform: uppercase; }
    .label { font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .count { background: #263958; border-radius: 999px; font-size: .75rem; min-width: 1.6rem; padding: .15rem .45rem; text-align: center; }
    .status { border: 1px solid #7185a5; border-radius: 999px; font-size: .72rem; padding: .2rem .45rem; white-space: nowrap; }
    .status[data-state='blocked'], .status[data-state='failed'] { border-color: #e66875; color: #ffb9c0; }
    .status[data-state='active'], .status[data-state='completed'] { border-color: #5ab98a; color: #a2ecc2; }
    .status.neutral, .more { color: #8798b6; }
    .more { font-size: .72rem; } footer { color: #9eafd0; display: flex; gap: 1rem; justify-content: space-between; }
    footer button { background: #263958; border: 1px solid #4d658d; border-radius: .4rem; color: white; padding: .45rem .7rem; }
    .empty { color: #9eafd0; padding: 2rem; text-align: center; }
    @media (max-width: 880px) { .tree-row { grid-template-columns: 1.2rem 5rem minmax(8rem, 1fr) auto; } .status, .more { grid-column: 3 / -1; } }
  `],
})
export class OrganizationHierarchyComponent {
  readonly state = inject(OrganizationTopologyStateService);
  readonly treeItems = viewChildren<ElementRef<HTMLButtonElement>>('treeitem');
  readonly collapsed = signal<ReadonlySet<string>>(new Set());
  readonly expandedLazy = signal<ReadonlySet<string>>(new Set());
  readonly effectiveCollapsed = computed(() => {
    const collapsed = new Set(this.collapsed());
    const nodes = this.state.visibleNodes();
    const loadedParents = new Set(
      nodes.map(node => node.parent_id).filter((value): value is string => Boolean(value)),
    );
    for (const node of nodes) {
      if (
        node.has_more_children
        && !loadedParents.has(node.id)
        && !this.expandedLazy().has(node.id)
      ) {
        collapsed.add(node.id);
      }
    }
    return collapsed;
  });
  readonly rows = computed(() => visibleTreeRows(
    this.state.visibleNodes(),
    this.effectiveCollapsed(),
  ));

  select(node: OrganizationTopologyNode): void {
    this.state.selectNode(node.id);
  }

  toggle(event: Event, node: OrganizationTopologyNode): void {
    event.stopPropagation();
    if (!node.child_count) return;
    const willExpand = this.effectiveCollapsed().has(node.id);
    this.collapsed.update(current => {
      const next = new Set(current);
      if (willExpand) next.delete(node.id); else next.add(node.id);
      return next;
    });
    if (willExpand && node.has_more_children) {
      this.expandedLazy.update(current => new Set(current).add(node.id));
      this.state.loadChildren(node.id);
    }
  }

  onKeydown(event: KeyboardEvent, index: number, node: OrganizationTopologyNode): void {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const direction = event.key === 'ArrowDown' ? 1 : -1;
      const target = Math.min(this.rows().length - 1, Math.max(0, index + direction));
      this.state.selectNode(this.rows()[target]?.id ?? null);
      queueMicrotask(() => this.treeItems()[target]?.nativeElement.focus());
      return;
    }
    if (event.key === 'ArrowRight' && node.child_count && this.effectiveCollapsed().has(node.id)) {
      event.preventDefault();
      this.toggle(event, node);
      return;
    }
    if (event.key === 'ArrowLeft' && node.child_count && !this.effectiveCollapsed().has(node.id)) {
      event.preventDefault();
      this.toggle(event, node);
      return;
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      this.select(node);
    }
  }

  runtimeLabel(nodeId: string): { state: string; label: string } | null {
    const status = this.state.topology()?.runtime_overlay?.nodes.find(item => item.node_id === nodeId)?.status;
    return status ? { state: status.state, label: status.label } : null;
  }

  kindLabel(kind: OrganizationTopologyNode['kind']): string {
    return ({
      organization: 'Organisation',
      coordination_unit: 'Koordination',
      value_stream: 'Value Stream',
      team: 'Team',
      role_slot: 'Rolle',
      assignment: 'Zuweisung',
    } as const)[kind];
  }
}

function visibleTreeRows(
  nodes: readonly OrganizationTopologyNode[],
  collapsed: ReadonlySet<string>,
): readonly OrganizationTopologyNode[] {
  const byParent = new Map<string | null, OrganizationTopologyNode[]>();
  nodes.forEach(node => {
    const parent = node.parent_id ?? null;
    const siblings = byParent.get(parent) ?? [];
    siblings.push(node);
    byParent.set(parent, siblings);
  });
  byParent.forEach(siblings => siblings.sort((left, right) => left.label.localeCompare(right.label)));

  const result: OrganizationTopologyNode[] = [];
  const visit = (node: OrganizationTopologyNode) => {
    result.push(node);
    if (collapsed.has(node.id)) return;
    (byParent.get(node.id) ?? []).forEach(visit);
  };
  const roots = [
    ...(byParent.get(null) ?? []),
    ...nodes.filter(node => node.parent_id && !nodes.some(candidate => candidate.id === node.parent_id)),
  ];
  [...new Map(roots.map(node => [node.id, node])).values()].forEach(visit);
  return result;
}
