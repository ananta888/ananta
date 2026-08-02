import { CommonModule } from '@angular/common';
import { Component, computed, inject } from '@angular/core';

import { OrganizationPlanningNode } from '../models/organization-topology.models';
import { OrganizationTopologyStateService } from '../services/organization-topology-state.service';

@Component({
  selector: 'app-organization-planning-hierarchy',
  standalone: true,
  imports: [CommonModule],
  template: `
    <section class="planning" aria-labelledby="organization-planning-heading">
      <header>
        <div><p class="eyebrow">Goal → Category-Todo → Track → Task</p><h2 id="organization-planning-heading">Planungslineage</h2></div>
        <button type="button" (click)="state.loadPlanning()" [disabled]="state.loading()">Neu laden</button>
      </header>

      @if (state.planning(); as planning) {
        <div class="lineage" role="tree" aria-label="Zweistufige Planungslineage">
          @for (node of nodes(); track node.id) {
            <article
              role="treeitem"
              [attr.aria-level]="depth(node) + 1"
              [style.--depth]="depth(node)"
              [attr.data-status]="node.status">
              <span class="kind">{{ kindLabel(node.kind) }}</span>
              <strong>{{ node.label }}</strong>
              <span class="status">{{ node.status }}</span>
              @if (node.revision) { <small>Revision {{ node.revision }}</small> }
              @if (node.digest) { <code>{{ node.digest }}</code> }
              @if (node.source_category_item_ids?.length) { <small>Quelle: {{ node.source_category_item_ids!.join(', ') }}</small> }
              @if (node.kind === 'category_todo' && node.status === 'validated' && node.revision && node.digest) {
                <button type="button" (click)="transition(node, 'promote')" [disabled]="state.mutating()">Exakt promoten</button>
              }
              @if (node.kind === 'planning_track' && (node.status === 'valid' || node.status === 'approved') && node.revision && node.digest) {
                <button type="button" (click)="transition(node, 'adopt')" [disabled]="state.mutating()">Exakt adoptieren</button>
              }
            </article>
          } @empty { <p class="empty">Noch keine Planungsartefakte vorhanden.</p> }
        </div>

        <section class="proposals" aria-labelledby="worker-proposals-heading">
          <h3 id="worker-proposals-heading">Worker-Task-Proposals</h3>
          <p>Vorschläge sind unverbindlich. Nur der Hub ordnet sie ein und wählt Rolle, Team oder Agent.</p>
          <div class="proposal-grid">
            @for (proposal of planning.proposals; track proposal.proposal_id) {
              <article [attr.data-status]="proposal.status">
                <header><strong>{{ proposal.proposal_id }}</strong><span>{{ proposal.status }}</span></header>
                <dl>
                  <div><dt>Source Task</dt><dd>{{ proposal.source_task_id }}</dd></div>
                  <div><dt>Proposer Role Slot</dt><dd>{{ proposal.proposer_role_slot_id }}</dd></div>
                  <div><dt>Zielhinweise</dt><dd>{{ hints(proposal) }}</dd></div>
                  <div><dt>Hub-Auswahl</dt><dd>{{ selection(proposal) }}</dd></div>
                  <div><dt>Policy</dt><dd><code>{{ proposal.policy_hash }}</code></dd></div>
                  @if (proposal.reason_code) { <div><dt>Grund</dt><dd>{{ proposal.reason_code }}</dd></div> }
                  @if (proposal.approval_id) { <div><dt>Approval</dt><dd>{{ proposal.approval_id }}</dd></div> }
                </dl>
                @if (proposal.status === 'needs_approval') {
                  <div class="actions">
                    <button type="button" class="reject" (click)="decide(proposal.proposal_id, 'reject')">Ablehnen</button>
                    <button type="button" (click)="decide(proposal.proposal_id, 'approve')">Gebunden freigeben</button>
                  </div>
                }
              </article>
            } @empty { <p class="empty">Keine offenen Worker-Task-Proposals.</p> }
          </div>
        </section>
      } @else {
        <div class="empty-state"><p>Lineage und Proposals wurden noch nicht geladen.</p><button type="button" (click)="state.loadPlanning()">Planung laden</button></div>
      }
    </section>
  `,
  styles: [`
    .planning { display: grid; gap: 1rem; } header { align-items: end; display: flex; justify-content: space-between; gap: 1rem; }
    h2, h3, p { margin: 0; } .eyebrow { color: #76a9ff; font-size: .72rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
    button { background: #2c70c8; border: 0; border-radius: .4rem; color: white; cursor: pointer; padding: .45rem .65rem; } button:disabled { opacity: .45; }
    .lineage { background: #0b1525; border: 1px solid #2d4160; border-radius: .7rem; display: grid; gap: .25rem; max-height: 58vh; overflow: auto; padding: .55rem; }
    .lineage article { --depth: 0; align-items: center; background: #101f34; border-left: 4px solid #527bb0; border-radius: .35rem; display: grid; gap: .5rem; grid-template-columns: 7rem minmax(170px, 1fr) auto auto minmax(120px, .8fr) auto; margin-left: calc(var(--depth) * 1.15rem); padding: .45rem; }
    .lineage article[data-status='invalid'], .lineage article[data-status='degraded'], .lineage article[data-status='rejected'] { border-color: #e36c77; }
    .lineage article[data-status='superseded'], .lineage article[data-status='stale'] { opacity: .68; }
    .kind { color: #8facd4; font-size: .68rem; text-transform: uppercase; } .status { border: 1px solid #5d789e; border-radius: 999px; font-size: .68rem; padding: .15rem .4rem; }
    code, small { color: #93a8c7; font-size: .67rem; overflow-wrap: anywhere; }
    .proposals { display: grid; gap: .6rem; } .proposal-grid { display: grid; gap: .6rem; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); }
    .proposal-grid > article { background: #0e1a2d; border: 1px solid #304769; border-radius: .6rem; display: grid; gap: .55rem; padding: .7rem; }
    .proposal-grid article[data-status='rejected'], .proposal-grid article[data-status='superseded'] { opacity: .68; }
    .proposal-grid header { align-items: center; } .proposal-grid header span { color: #9db0cf; font-size: .72rem; }
    dl { display: grid; gap: .3rem; margin: 0; } dl div { background: #101f34; padding: .35rem; } dt { color: #91a5c4; font-size: .66rem; } dd { margin: .1rem 0 0; overflow-wrap: anywhere; }
    .actions { display: flex; gap: .4rem; justify-content: flex-end; } button.reject { background: #743441; }
    .empty, .empty-state { color: #91a5c4; padding: 1rem; text-align: center; } @media (max-width: 900px) { .lineage article { grid-template-columns: 5rem 1fr auto; } }
  `],
})
export class OrganizationPlanningHierarchyComponent {
  readonly state = inject(OrganizationTopologyStateService);
  readonly nodes = computed(() => topologicalPlanningOrder(this.state.planning()?.nodes ?? []));

  depth(node: OrganizationPlanningNode): number {
    const byId = new Map((this.state.planning()?.nodes ?? []).map(item => [item.id, item]));
    let depth = 0;
    let current = node;
    const seen = new Set<string>();
    while (current.parent_id && byId.has(current.parent_id) && !seen.has(current.id)) {
      seen.add(current.id);
      current = byId.get(current.parent_id)!;
      depth += 1;
    }
    return depth;
  }

  transition(node: OrganizationPlanningNode, operation: 'promote' | 'adopt'): void {
    if (!node.revision || !node.digest) return;
    this.state.transitionPlanningArtifact(node.id, operation, node.revision, node.digest);
  }

  decide(proposalId: string, operation: 'approve' | 'reject'): void {
    const proposal = this.state.planning()?.proposals.find(candidate => candidate.proposal_id === proposalId);
    if (!proposal?.revision || !proposal.digest) {
      this.state.error.set('Proposal-Entscheidung benötigt die exakte Proposal-Revision und ihren Digest.');
      return;
    }
    this.state.decideProposal(proposalId, operation, proposal.revision, proposal.digest);
  }

  hints(proposal: { target_role_hint?: string; target_team_hint?: string; target_agent_hint?: string }): string {
    return [proposal.target_role_hint, proposal.target_team_hint, proposal.target_agent_hint].filter(Boolean).join(' · ') || 'keine';
  }

  selection(proposal: { selected_role_slot_id?: string; selected_team_id?: string; selected_agent_id?: string }): string {
    return [proposal.selected_role_slot_id, proposal.selected_team_id, proposal.selected_agent_id].filter(Boolean).join(' · ') || 'noch offen';
  }

  kindLabel(kind: OrganizationPlanningNode['kind']): string {
    return ({ goal: 'Goal', category_todo: 'Category-Todo', planning_track: 'Track', milestone: 'Milestone', task: 'Task' } as const)[kind];
  }
}

function topologicalPlanningOrder(nodes: readonly OrganizationPlanningNode[]): readonly OrganizationPlanningNode[] {
  const children = new Map<string | null, OrganizationPlanningNode[]>();
  nodes.forEach(node => {
    const parent = node.parent_id ?? null;
    children.set(parent, [...(children.get(parent) ?? []), node]);
  });
  const result: OrganizationPlanningNode[] = [];
  const visit = (node: OrganizationPlanningNode) => {
    result.push(node);
    (children.get(node.id) ?? []).forEach(visit);
  };
  const roots = nodes.filter(node => !node.parent_id || !nodes.some(candidate => candidate.id === node.parent_id));
  roots.forEach(visit);
  return result;
}
