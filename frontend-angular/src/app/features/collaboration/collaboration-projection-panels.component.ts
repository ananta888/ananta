import { JsonPipe } from '@angular/common';
import { Component, Input } from '@angular/core';

import { CollaborationFlowProjection, CollaborationResourceOffer } from './collaboration-workspace.models';

type ProjectionEntry = [string, Record<string, unknown>];

@Component({
  selector: 'app-collaboration-projection-panels',
  standalone: true,
  imports: [JsonPipe],
  template: `
    @if (flow) {
      <small>Checkpoint {{ flow.checkpoint }} · Digest {{ flow.state_digest }}</small>
      <section aria-label="Task projections">
        <h3>Tasks und Workflows</h3>
        @for (task of entries(flow.state.tasks); track task[0]) {
          <article><strong>Task · {{ task[0] }}</strong><span>{{ task[1] | json }}</span></article>
        }
        @for (workflow of entries(flow.state.workflows); track workflow[0]) {
          <article><strong>Workflow · {{ workflow[0] }}</strong><span>{{ workflow[1] | json }}</span></article>
        }
      </section>
      <section aria-label="Git projections">
        <h3>Git</h3>
        @for (ref of entries(flow.state.git_refs); track ref[0]) {
          <article><strong>{{ ref[0] }}</strong><span>{{ ref[1] | json }}</span></article>
        }
      </section>
      <section aria-label="Review and artifact projections">
        <h3>Reviews und Artifacts</h3>
        @for (review of entries(flow.state.reviews); track review[0]) {
          <article><strong>Review · {{ review[0] }}</strong><span>{{ review[1] | json }}</span></article>
        }
        @for (artifact of entries(flow.state.artifacts); track artifact[0]) {
          <article>
            <strong>Artifact · {{ artifact[0] }}</strong>
            <span>Digest {{ artifact[1]['digest'] || 'unverified' }} · Version {{ artifact[1]['version'] || artifact[1]['workspace_sequence'] || '—' }}</span>
            <small>Scope {{ artifact[1]['scope'] || 'workspace' }} · Verfügbarkeit {{ artifact[1]['availability'] || 'partial' }}</small>
          </article>
        }
      </section>
      <section aria-label="CodeCompass projections">
        <h3>CodeCompass</h3>
        @for (context of codeCompassEntries(); track context[0]) {
          <article>
            <strong>{{ context[0] }}</strong>
            <span>Digest {{ context[1]['graph_digest'] || context[1]['source_digest'] || 'unverified' }}</span>
            <small>Scope {{ context[1]['source_id'] || 'unknown' }} · {{ context[1]['completeness'] || 'partial' }}</small>
          </article>
        }
      </section>
      <section aria-label="Terminal resource projections">
        <h3>Terminals</h3>
        @for (offer of terminalOffers(); track offer.offer_id) {
          <article>
            <strong>{{ offer.resource_id }} · {{ offer.capacity_class }}</strong>
            <span>Scopes {{ offer.scopes.join(', ') }}</span>
            <small>Attestation {{ offer.attestation_status }} · Digest {{ offer.payload_digest || 'unverified' }}</small>
          </article>
        }
      </section>
      @if (!hasEntries()) { <p>Noch keine belegten Fachprojektionen.</p> }
    } @else {
      <p>Workspace auswählen, um permission-gefilterte Projektionen zu laden.</p>
    }
  `,
  styles: [`
    :host, section { display:grid; gap:8px; }
    section { margin-block:12px; }
    h3 { margin:0; }
    article { display:grid; gap:4px; padding:8px; border:1px solid var(--border-color); border-radius:6px; }
  `],
})
export class CollaborationProjectionPanelsComponent {
  @Input() flow: CollaborationFlowProjection | null = null;
  @Input() offers: CollaborationResourceOffer[] = [];

  entries(value: Record<string, Record<string, unknown>>): ProjectionEntry[] {
    return Object.entries(value);
  }

  terminalOffers(): CollaborationResourceOffer[] {
    return this.offers.filter(offer => offer.capability_category === 'terminal');
  }

  codeCompassEntries(): ProjectionEntry[] {
    if (!this.flow) return [];
    const result: ProjectionEntry[] = [];
    for (const collection of Object.values(this.flow.state)) {
      for (const [identifier, projection] of Object.entries(collection)) {
        const context = projection['codecompass'];
        if (context && typeof context === 'object' && !Array.isArray(context)) {
          result.push([identifier, context as Record<string, unknown>]);
        }
      }
    }
    return result;
  }

  hasEntries(): boolean {
    if (!this.flow) return false;
    return Object.values(this.flow.state).some(value => Object.keys(value).length > 0) || this.terminalOffers().length > 0;
  }
}
