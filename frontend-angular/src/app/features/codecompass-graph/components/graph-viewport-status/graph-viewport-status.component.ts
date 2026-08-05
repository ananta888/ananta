import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

import {
  GraphArtifactIssue,
  GraphProjectionIssue,
  GraphSemanticIssue,
  GraphViewportSummary,
} from '../../models/graph-viewport-summary.model';

@Component({
  standalone: true,
  selector: 'app-graph-viewport-status',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (summary; as current) {
      <section class="viewport-status" data-testid="graph-viewport-status"
               aria-label="Umfang und Vollständigkeit des Graphen">
        <div class="statistics" aria-label="Graphstatistiken">
          <article class="stat-card" data-testid="graph-stats-total">
            <span class="stat-title">Gesamtindex</span>
            <strong>{{ formatCount(current.nodes.total) }} Knoten</strong>
            <span>{{ formatCount(current.edges.total) }} materialisierte Relationen</span>
          </article>

          <article class="stat-card" data-testid="graph-stats-window">
            <span class="stat-title">Geladenes Fenster</span>
            <strong>{{ formatCount(current.nodes.loaded) }} Knoten</strong>
            <span>
              {{ formatCount(current.edges.loaded) }} Relationen
              @if (showInternalEdgeCount(current)) {
                <small>von {{ formatCount(current.edges.internalWindow) }} im Knotenfenster</small>
              }
            </span>
          </article>

          <article class="stat-card" data-testid="graph-stats-visible">
            <span class="stat-title">Aktuelle Ansicht</span>
            <strong>{{ formatCount(current.nodes.visible) }} Knoten</strong>
            <span>{{ formatCount(current.edges.visible) }} Relationen</span>
          </article>

          <article class="stat-card" data-testid="graph-stats-inventory">
            <span class="stat-title">Inventar im Fenster</span>
            <strong>{{ current.domains.visible }}/{{ current.domains.loaded }} Domains</strong>
            <span>
              {{ current.rawRelationTypes.visible }}/{{ current.rawRelationTypes.loaded }} Relationstypen
              <small>sichtbar / geladen</small>
            </span>
          </article>
        </div>

        @if (hasSemanticWarning(current)) {
          <div class="notice notice-warning" data-testid="graph-semantic-warning" role="status">
            <div class="notice-heading">
              <span aria-hidden="true">⚠</span>
              <strong>Semantischer Graph unvollständig</strong>
            </div>
            <ul>
              @for (issue of current.semanticIssues; track issue.code) {
                <li>{{ semanticIssueText(issue) }}</li>
              }
              @for (issue of current.artifactIssues; track issue.code) {
                <li>{{ artifactIssueText(issue) }}</li>
              }
            </ul>
          </div>
        }

        @if (current.projectionIssues.length) {
          <div class="notice notice-info" data-testid="graph-window-warning" role="status">
            <div class="notice-heading">
              <span aria-hidden="true">ⓘ</span>
              <strong>Begrenztes Topologie-Fenster</strong>
            </div>
            <ul>
              @for (issue of current.projectionIssues; track issue.code) {
                <li>{{ projectionIssueText(issue) }}</li>
              }
            </ul>
            <p>
              Domain-, Relations- und Tiefenfilter arbeiten innerhalb des geladenen Fensters.
            </p>
          </div>
        }

        @if (current.rawWarnings.length) {
          <details class="raw-warnings">
            <summary>Technische Hinweise des Indexers ({{ current.rawWarnings.length }})</summary>
            <ul>
              @for (warning of current.rawWarnings; track $index) {
                <li>{{ warning }}</li>
              }
            </ul>
          </details>
        }
      </section>
    }
  `,
  styles: [`
    :host { display: block; flex-shrink: 0; }
    .viewport-status {
      display: grid;
      gap: var(--space-sm);
      padding: var(--space-sm);
      border-bottom: 1px solid var(--border);
      background: var(--surface-soft);
      color: var(--fg);
    }
    .statistics {
      display: grid;
      grid-template-columns: repeat(4, minmax(145px, 1fr));
      gap: var(--space-sm);
    }
    .stat-card {
      display: grid;
      gap: 2px;
      min-width: 0;
      padding: 7px 9px;
      border: 1px solid var(--border);
      border-radius: var(--radius-control);
      background: var(--surface-raised);
      font-size: .72rem;
      line-height: 1.35;
      font-variant-numeric: tabular-nums;
    }
    .stat-title {
      color: var(--muted);
      font-size: .65rem;
      font-weight: 700;
      letter-spacing: .04em;
      text-transform: uppercase;
    }
    .stat-card strong { color: var(--fg); font-size: .82rem; }
    .stat-card small { display: block; color: var(--muted); font-size: .64rem; }
    .notice {
      padding: 7px 9px;
      border: 1px solid;
      border-left-width: 4px;
      border-radius: var(--radius-control);
      font-size: .74rem;
      line-height: 1.4;
    }
    .notice-warning {
      border-color: var(--tone-warning);
      background: color-mix(in srgb, var(--tone-warning) 12%, var(--surface-raised));
      color: var(--fg);
    }
    .notice-info {
      border-color: var(--tone-info);
      background: color-mix(in srgb, var(--tone-info) 9%, var(--surface-raised));
      color: var(--fg);
    }
    .notice-heading { display: flex; align-items: center; gap: 6px; }
    .notice ul { margin: 4px 0 0; padding-left: 1.2rem; }
    .notice p { margin: 4px 0 0; }
    .raw-warnings { color: var(--muted); font-size: .7rem; }
    .raw-warnings summary { cursor: pointer; font-weight: 650; }
    .raw-warnings ul { margin: 5px 0 0; padding-left: 1.2rem; overflow-wrap: anywhere; }
    @media (max-width: 850px) {
      .statistics { grid-template-columns: repeat(2, minmax(130px, 1fr)); }
    }
    @media (max-width: 470px) {
      .statistics { grid-template-columns: 1fr; }
    }
  `],
})
export class GraphViewportStatusComponent {
  @Input() summary: Readonly<GraphViewportSummary> | null = null;

  formatCount(value: number | null): string {
    return value === null ? 'nicht gemeldet' : value.toLocaleString('de-DE');
  }

  showInternalEdgeCount(summary: Readonly<GraphViewportSummary>): boolean {
    const internal = summary.edges.internalWindow;
    return internal !== null && internal !== summary.edges.loaded;
  }

  hasSemanticWarning(summary: Readonly<GraphViewportSummary>): boolean {
    return summary.semanticIssues.length > 0 || summary.artifactIssues.length > 0;
  }

  semanticIssueText(issue: Readonly<GraphSemanticIssue>): string {
    switch (issue.code) {
      case 'semantic_translation_unavailable':
        return 'Die semantische Übersetzung steht für diesen Index nicht zur Verfügung.';
      case 'semantic_graph_truncated':
        return issue.affectedCount === null
          ? 'Die semantische Übersetzung wurde durch ihr Verarbeitungsbudget begrenzt.'
          : `${this.formatCount(issue.affectedCount)} Datensätze wurden laut Diagnose durch das semantische Budget begrenzt.`;
      case 'semantic_graph_candidate_relations_truncated':
        return issue.affectedCount === null
          ? 'Ein Teil der Kandidatenrelationen liegt außerhalb des semantischen Budgets.'
          : `Davon liegen ${this.formatCount(issue.affectedCount)} Kandidatenrelationen außerhalb des Verarbeitungsbudgets.`;
      case 'semantic_graph_relations_unresolved':
        return issue.affectedCount === null
          ? 'Semantische Relationen konnten nicht vollständig aufgelöst werden.'
          : `${this.formatCount(issue.affectedCount)} semantische Relationen konnten nicht aufgelöst werden.`;
      case 'semantic_graph_degraded':
        return 'Der Indexer meldet die semantische Übersetzung als teilweise verfügbar.';
    }
  }

  artifactIssueText(issue: Readonly<GraphArtifactIssue>): string {
    switch (issue.code) {
      case 'graph_artifact_unavailable':
        return 'Das zugehörige Graph-Artefakt steht nicht vollständig zur Verfügung.';
    }
  }

  projectionIssueText(issue: Readonly<GraphProjectionIssue>): string {
    switch (issue.code) {
      case 'graph_node_window_bounded':
        return issue.affectedCount === null
          ? 'Der Server hat nur ein begrenztes Knotenfenster geladen.'
          : `${this.formatCount(issue.affectedCount)} weitere Knoten liegen außerhalb des geladenen Fensters.`;
      case 'graph_edge_window_capped':
        return issue.affectedCount === null
          ? 'Die Relationen im geladenen Knotenfenster wurden serverseitig begrenzt.'
          : `${this.formatCount(issue.affectedCount)} Relationen im Knotenfenster wurden nicht mitgeladen.`;
      case 'graph_relations_unresolved':
        return issue.affectedCount === null
          ? 'Relationen des Indexes konnten nicht aufgelöst werden.'
          : `${this.formatCount(issue.affectedCount)} Relationen des Indexes konnten nicht aufgelöst werden.`;
      case 'graph_window_evidence_inconsistent':
        return 'Die gemeldeten Fensterzahlen widersprechen den geladenen Daten; Gesamtwerte werden vorsichtig behandelt.';
    }
  }
}
