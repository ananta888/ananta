import { CommonModule } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { forkJoin, of } from 'rxjs';
import { catchError, map } from 'rxjs/operators';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { ProjectContextService } from '../../../services/project-context.service';
import type {
  OrganizationSummary,
  OrganizationTopologyPage,
} from '../../organizations/models/organization-topology.models';
import { OrganizationApiClient } from '../../organizations/services/organization-api.client';
import {
  VisualProcessApiService,
  type SavedGraphSummary,
  type VpGraph,
} from '../../visual-process/visual-process-api.service';
import { CaseFlowAgentLiveViewComponent } from './caseflow-agent-live-view.component';
import {
  type OverviewCard,
  type OverviewStructureNode,
  childrenOf,
  countByLevel,
  descendantsOf,
  matchesSearch,
  organizationCard,
  structureNodes,
  teamCard,
  workScopeFor,
} from './caseflow-overview.models';
import { CaseFlowWorkPanelComponent } from './caseflow-work-panel.component';
import type { WorkScope } from './caseflow-work.models';

type Level = 'all' | 'organization' | 'team';

/**
 * Everything, on one screen, at whatever size a person is thinking in.
 *
 * An organisation and an agent team are the same idea at two scales, and
 * until now they lived in two screens that never mentioned each other: one
 * for compiling an organisation out of blueprints, one for a graph of agents.
 * Someone who simply wants to see what exists — and then watch it work — had
 * to know which subsystem owned what they were looking for.
 *
 * This asks nothing of them. Both levels arrive as the same card, marked with
 * what it is, and opening one leads down: an organisation into its structure,
 * a team onto the map where its agents can be watched and opened.
 */
@Component({
  selector: 'app-caseflow-overview',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, CaseFlowAgentLiveViewComponent, CaseFlowWorkPanelComponent],
  styleUrl: './caseflow-overview.component.scss',
  template: `
    <section class="ov">
      <header class="ov-head">
        <div>
          <h1>Übersicht</h1>
          <p class="ov-lead">
            Alles, was es gibt — Organisationen und Agenten-Teams — an einer Stelle. Ein Team öffnen,
            um seinen Agenten bei der Arbeit zuzusehen.
          </p>
        </div>
        @if (level() !== 'all') {
          <button type="button" class="ov-ghost" (click)="backToAll()">← Alle Ebenen</button>
        }
      </header>

      @for (message of errors(); track message) {
        <p class="ov-error" role="alert">{{ message }}</p>
      }

      @switch (level()) {
        @case ('all') {
          <div class="ov-search">
            <input
              type="search"
              placeholder="Organisation oder Team suchen …"
              [ngModel]="search()"
              (ngModelChange)="search.set($event)"
              aria-label="Organisation oder Team suchen"
            />
          </div>

          @if (loading()) {
            <p class="ov-muted">Wird geladen …</p>
          } @else if (!visibleCards().length) {
            <p class="ov-muted">
              Hier ist noch nichts. Ein Team lässt sich unter
              <a routerLink="/caseflow/team">Agenten-Team</a> aus einer Vorlage bauen.
            </p>
          } @else {
            <div class="ov-grid">
              @for (card of visibleCards(); track card.id) {
                <button type="button" class="ov-card" (click)="openCard(card)">
                  <span class="ov-card-glyph" aria-hidden="true">{{ card.glyph }}</span>
                  <span class="ov-card-level">{{ card.level_label }}</span>
                  <span class="ov-card-title">{{ card.title }}</span>
                  <span class="ov-card-sub">{{ card.subtitle }}</span>
                </button>
              }
            </div>
          }
        }

        @case ('organization') {
          @if (openOrganization(); as organization) {
            <nav class="ov-path" aria-label="Ebene">
              <button type="button" class="ov-crumb" (click)="zoomTo(-1)">
                <span aria-hidden="true">{{ organizationGlyph }}</span> {{ organization.title }}
              </button>
              @for (step of path(); track step.id) {
                <span class="ov-crumb-sep" aria-hidden="true">›</span>
                <button type="button" class="ov-crumb" (click)="zoomTo($index)">
                  <span aria-hidden="true">{{ step.glyph }}</span> {{ step.label }}
                </button>
              }
            </nav>

            @if (structureLoading()) {
              <p class="ov-muted">Struktur wird geladen …</p>
            } @else if (!structure().length) {
              <p class="ov-muted">Zu dieser Organisation ist keine Struktur lesbar.</p>
            } @else {
              <p class="ov-hint">
                {{ currentSummary() }}
                @if (!path().length) {
                  Für Umbauten geht es im
                  <a routerLink="/organizations">Organisations-Editor</a> weiter.
                }
              </p>

              @if (visibleParts().length) {
                <div class="ov-parts">
                  @for (part of visibleParts(); track part.id) {
                    <button
                      type="button"
                      class="ov-part"
                      [class.ov-part--leaf]="!hasChildren(part)"
                      (click)="zoomInto(part)"
                    >
                      <span class="ov-part-glyph" aria-hidden="true">{{ part.glyph }}</span>
                      <span class="ov-part-label">{{ part.label }}</span>
                      <span class="ov-part-meta">
                        @if (hasChildren(part)) {
                          <span class="ov-part-count">{{ contentsLabel(part) }}</span>
                        }
                        @if (part.status) {
                          <span class="ov-row-status">{{ part.status }}</span>
                        }
                      </span>
                    </button>
                  }
                </div>
              } @else {
                <p class="ov-muted">Diese Ebene enthält nichts Weiteres.</p>
              }

              <h3 class="ov-section ov-section--small">{{ currentTitle() }} — Arbeit</h3>
              <app-caseflow-work-panel [scope]="workScope()" />
            }
          }
        }

        @case ('team') {
          @if (openTeam(); as team) {
            <app-caseflow-agent-live-view [graph]="team" (graphChange)="openTeam.set($event)" />
            <h3 class="ov-section ov-section--small">Aufgaben dieses Teams</h3>
            <app-caseflow-work-panel [scope]="teamScope()" />
          } @else {
            <p class="ov-muted">Team wird geladen …</p>
          }
        }
      }
    </section>
  `,
})
export class CaseFlowOverviewComponent {
  private readonly organizations = inject(OrganizationApiClient);
  private readonly visualProcess = inject(VisualProcessApiService);
  private readonly directory = inject(AgentDirectoryService);
  private readonly projects = inject(ProjectContextService);

  protected readonly organizationGlyph = '🏛️';

  protected readonly level = signal<Level>('all');
  protected readonly search = signal('');
  protected readonly loading = signal(true);
  protected readonly structureLoading = signal(false);
  protected readonly errors = signal<readonly string[]>([]);
  protected readonly openOrganization = signal<OrganizationSummary | null>(null);
  protected readonly openTeam = signal<VpGraph | null>(null);

  private readonly organizationList = signal<readonly OrganizationSummary[]>([]);
  private readonly teamList = signal<readonly SavedGraphSummary[]>([]);
  private readonly topology = signal<OrganizationTopologyPage | null>(null);

  /** Teams first: that is the level a person works at day to day. */
  protected readonly cards = computed<readonly OverviewCard[]>(() => [
    ...this.teamList().map(teamCard),
    ...this.organizationList().map(organizationCard),
  ]);

  protected readonly visibleCards = computed(() =>
    this.cards().filter(card => matchesSearch(card, this.search())),
  );

  protected readonly structure = computed(() => structureNodes(this.topology()));

  /** The nodes clicked into, outermost first; empty means the organisation. */
  protected readonly path = signal<readonly OverviewStructureNode[]>([]);

  protected readonly currentNode = computed(() => this.path().at(-1) ?? null);

  /** What the level a person is standing on directly contains. */
  protected readonly visibleParts = computed(() =>
    childrenOf(this.structure(), this.currentNode()?.id ?? null),
  );

  protected readonly workScope = computed<WorkScope>(() => workScopeFor(this.currentNode()));

  protected readonly teamScope = computed<WorkScope>(() => ({
    level: 'team',
    team_id: this.openTeam()?.id ?? '',
  }));

  protected readonly structureSummary = computed(() => {
    const counts = countByLevel(this.structure());
    const parts = [
      counts['unit'] ? `${counts['unit']} Bereiche` : null,
      counts['value_stream'] ? `${counts['value_stream']} Wertströme` : null,
      counts['team'] ? `${counts['team']} Teams` : null,
      counts['role_slot'] ? `${counts['role_slot']} Rollenplätze` : null,
      counts['agent'] ? `${counts['agent']} Besetzungen` : null,
    ].filter(Boolean);
    return parts.join(' · ');
  });

  /** What the current level holds, counted over everything below it. */
  protected readonly currentSummary = computed(() => {
    const node = this.currentNode();
    const scoped = node ? descendantsOf(this.structure(), node.id) : this.structure();
    const counts = countByLevel(scoped);
    const parts = [
      counts['unit'] ? `${counts['unit']} Bereiche` : null,
      counts['value_stream'] ? `${counts['value_stream']} Wertströme` : null,
      counts['team'] ? `${counts['team']} Teams` : null,
      counts['role_slot'] ? `${counts['role_slot']} Rollenplätze` : null,
      counts['agent'] ? `${counts['agent']} Besetzungen` : null,
    ].filter(Boolean);
    return parts.length ? parts.join(' · ') : 'Nichts weiter darunter.';
  });

  constructor() {
    this.load();
  }

  protected openCard(card: OverviewCard): void {
    if (card.level === 'organization') {
      const organization = this.organizationList().find(item => item.id === card.id);
      if (!organization) return;
      this.openOrganization.set(organization);
      this.path.set([]);
      this.level.set('organization');
      this.loadTopology(organization.id);
      return;
    }
    this.openTeamById(card.id);
  }

  /**
   * Click into one part; what is below it becomes the whole view.
   *
   * A part with nothing under it is still worth opening: its own work is the
   * point of the click, not its contents.
   */
  protected zoomInto(node: OverviewStructureNode): void {
    this.path.set([...this.path(), node]);
  }

  /** Back up to a level already visited; -1 is the organisation itself. */
  protected zoomTo(index: number): void {
    this.path.set(index < 0 ? [] : this.path().slice(0, index + 1));
  }

  protected hasChildren(node: OverviewStructureNode): boolean {
    return childrenOf(this.structure(), node.id).length > 0;
  }

  protected contentsLabel(node: OverviewStructureNode): string {
    const count = childrenOf(this.structure(), node.id).length;
    return count === 1 ? '1 Teil' : `${count} Teile`;
  }

  protected currentTitle(): string {
    return this.currentNode()?.label ?? this.openOrganization()?.title ?? 'Organisation';
  }

  protected backToAll(): void {
    this.level.set('all');
    this.openOrganization.set(null);
    this.openTeam.set(null);
    this.topology.set(null);
    this.path.set([]);
  }

  private openTeamById(graphId: string): void {
    this.openTeam.set(null);
    this.level.set('team');
    this.visualProcess.loadSavedGraph(graphId).subscribe({
      next: graph => this.openTeam.set(graph),
      error: () => {
        this.level.set('all');
        this.report('Dieses Team konnte nicht geladen werden.');
      },
    });
  }

  /**
   * Read both levels at once, and let either fail on its own.
   *
   * A missing organisation catalog must not hide the teams, and vice versa:
   * these are separate subsystems and one being down is not a reason to show
   * a person nothing.
   */
  private load(): void {
    const hubUrl = this.hubUrl();
    const projectId = this.projects.selectedProjectId();
    forkJoin({
      teams: this.visualProcess.listSavedGraphs().pipe(catchError(() => of(null))),
      organizations:
        hubUrl && projectId
          ? this.organizations.listOrganizations(hubUrl, projectId, '', 100).pipe(
              map(page => (Array.isArray(page) ? page : page?.items) ?? []),
              catchError(() => of(null)),
            )
          : of([] as readonly OrganizationSummary[]),
    }).subscribe(({ teams, organizations }) => {
      this.loading.set(false);
      if (teams === null) this.report('Die Teams konnten nicht geladen werden.');
      else this.teamList.set(teams);
      if (organizations === null) this.report('Die Organisationen konnten nicht geladen werden.');
      else this.organizationList.set(organizations as readonly OrganizationSummary[]);
      if (!projectId) {
        this.report('Ohne gewähltes Projekt werden keine Organisationen angezeigt.');
      }
    });
  }

  private loadTopology(organizationId: string): void {
    const hubUrl = this.hubUrl();
    if (!hubUrl) {
      this.report('Kein Hub konfiguriert.');
      return;
    }
    this.topology.set(null);
    this.path.set([]);
    this.structureLoading.set(true);
    this.organizations
      .topology(hubUrl, organizationId, { page_size: 100, include_runtime: true })
      .subscribe({
        next: page => {
          this.structureLoading.set(false);
          this.topology.set(page);
        },
        error: () => {
          this.structureLoading.set(false);
          this.report('Die Struktur dieser Organisation konnte nicht gelesen werden.');
        },
      });
  }

  private hubUrl(): string {
    return this.directory.list().find(agent => agent.role === 'hub')?.url ?? '';
  }

  private report(message: string): void {
    if (this.errors().includes(message)) return;
    this.errors.set([...this.errors(), message]);
  }
}
