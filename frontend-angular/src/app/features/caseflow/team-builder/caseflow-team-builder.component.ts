import { CommonModule } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import type { VpGraph } from '../../visual-process/visual-process-api.service';
import { VisualProcessApiService } from '../../visual-process/visual-process-api.service';
import type { SavedGraphSummary } from '../../visual-process/visual-process-api.service';
import { CaseFlowAgentLiveViewComponent } from './caseflow-agent-live-view.component';
import {
  TEAM_TEMPLATE_KIND_TEAM,
  type TeamDraft,
  type TeamTemplate,
  buildGraph,
  draftFromTemplate,
  isSmallTeam,
  validateDraft,
  withAddedAgent,
  withRemovedAgent,
  withRenamedAgent,
} from './caseflow-team-builder.models';
import { type CatalogRole, CaseFlowTeamBuilderService } from './caseflow-team-builder.service';

type Phase = 'gallery' | 'draft' | 'team';

/**
 * Assembling a small team of agents, and watching it work.
 *
 * Three editors already exist for this graph, and each of them asks a person
 * to know something before it helps: which process, which node kind, which
 * notation. This one asks which template and what the team is called, then
 * hands the same graph to those editors for anything finer. Nothing here is
 * a separate format — a team saved here opens in all three.
 */
@Component({
  selector: 'app-caseflow-team-builder',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, CaseFlowAgentLiveViewComponent],
  styleUrl: './caseflow-team-builder.component.scss',
  template: `
    <section class="team-builder">
      <header class="tb-head">
        <div>
          <h1>Agenten-Team</h1>
          <p class="tb-lead">
            Ein Team aus 3 bis 10 Agenten zusammenstellen, benennen und beim Arbeiten zusehen.
            Für feinere Einstellungen öffnen die anderen Editoren dasselbe Team weiter.
          </p>
        </div>
        @if (phase() !== 'gallery') {
          <button type="button" class="tb-ghost" (click)="backToGallery()">← Übersicht</button>
        }
      </header>

      @if (error(); as message) {
        <p class="tb-error" role="alert">{{ message }}</p>
      }

      @switch (phase()) {
        @case ('gallery') {
          <div class="tb-search">
            <input
              type="search"
              placeholder="Vorlage oder Team suchen …"
              [ngModel]="search()"
              (ngModelChange)="search.set($event)"
              aria-label="Vorlage oder Team suchen"
            />
          </div>

          @if (visibleTeams().length) {
            <h2 class="tb-section">Deine Teams</h2>
            <div class="tb-grid">
              @for (team of visibleTeams(); track team.id) {
                <button type="button" class="tb-card tb-card--saved" (click)="openTeam(team.id)">
                  <span class="tb-kind tb-kind--saved">Team</span>
                  <span class="tb-card-name">{{ team.name }}</span>
                  @if (team.description) {
                    <span class="tb-card-desc">{{ team.description }}</span>
                  }
                </button>
              }
            </div>
          }

          <h2 class="tb-section">Vorlagen</h2>
          <p class="tb-hint">
            <strong>Team</strong> sagt, wer zusammenarbeitet. <strong>Prozess</strong> sagt, wie die
            Arbeit fließt. Beides lässt sich danach ändern.
          </p>
          @if (loading()) {
            <p class="tb-muted">Vorlagen werden geladen …</p>
          } @else if (!visibleTemplates().length) {
            <p class="tb-muted">Dazu passt keine Vorlage.</p>
          } @else {
            <div class="tb-grid">
              @for (template of visibleTemplates(); track template.template_id) {
                <button type="button" class="tb-card" (click)="chooseTemplate(template)">
                  <span class="tb-kind" [class.tb-kind--process]="template.kind !== teamKind">
                    {{ template.kind === teamKind ? 'Team' : 'Prozess' }}
                  </span>
                  <span class="tb-card-name">{{ template.display_name }}</span>
                  @if (template.description) {
                    <span class="tb-card-desc">{{ template.description }}</span>
                  }
                  @if (template.agent_count) {
                    <span class="tb-card-count">{{ template.agent_count }} Agenten</span>
                    <span class="tb-roles">
                      @for (role of template.roles.slice(0, 5); track role.role_id) {
                        <span class="tb-role">{{ role.display_name }}</span>
                      }
                      @if (template.roles.length > 5) {
                        <span class="tb-role tb-role--more">+{{ template.roles.length - 5 }}</span>
                      }
                    </span>
                  } @else {
                    <span class="tb-card-count">Agenten selbst wählen</span>
                  }
                </button>
              }
            </div>
          }
        }

        @case ('draft') {
          @if (draft(); as current) {
            <div class="tb-draft">
              <label class="tb-field">
                <span>Wie heißt das Team?</span>
                <input
                  type="text"
                  [ngModel]="current.team_name"
                  (ngModelChange)="renameTeam($event)"
                  aria-label="Teamname"
                />
              </label>

              <h2 class="tb-section">Agenten</h2>
              <p class="tb-hint">
                Jeder Agent bekommt einen Namen, unter dem er später auftaucht. Die Reihenfolge ist
                die Reihenfolge, in der weitergegeben wird.
              </p>

              <ol class="tb-agents">
                @for (agent of current.agents; track agent.step_id) {
                  <li class="tb-agent" [class.tb-agent--bad]="issueFor(agent.step_id)">
                    <span class="tb-agent-index">{{ $index + 1 }}</span>
                    <input
                      type="text"
                      class="tb-agent-name"
                      [ngModel]="agent.name"
                      (ngModelChange)="renameAgent(agent.step_id, $event)"
                      [attr.aria-label]="'Name des ' + ($index + 1) + '. Agenten'"
                    />
                    <span class="tb-agent-role">{{ agent.role_name }}</span>
                    <button
                      type="button"
                      class="tb-ghost"
                      (click)="removeAgent(agent.step_id)"
                      [attr.aria-label]="'Agent ' + agent.name + ' entfernen'"
                    >
                      Entfernen
                    </button>
                    @if (issueFor(agent.step_id); as issue) {
                      <span class="tb-agent-issue">{{ issue }}</span>
                    }
                  </li>
                }
              </ol>

              <div class="tb-add">
                <label class="tb-field tb-field--inline">
                  <span>Rolle hinzufügen</span>
                  <select [ngModel]="roleToAdd()" (ngModelChange)="roleToAdd.set($event)" aria-label="Rolle">
                    <option value="">Rolle wählen …</option>
                    @for (role of roles(); track role.id) {
                      <option [value]="role.id">{{ role.name }}</option>
                    }
                  </select>
                </label>
                <button type="button" class="tb-ghost" [disabled]="!roleToAdd()" (click)="addAgent()">
                  Agent hinzufügen
                </button>
              </div>

              @for (issue of teamIssues(); track issue) {
                <p class="tb-error">{{ issue }}</p>
              }

              <div class="tb-actions">
                <button type="button" class="tb-primary" [disabled]="!canSave() || saving()" (click)="save()">
                  {{ saving() ? 'Wird angelegt …' : 'Team anlegen' }}
                </button>
                <button type="button" class="tb-ghost" (click)="backToGallery()">Abbrechen</button>
              </div>
            </div>
          }
        }

        @case ('team') {
          @if (graph(); as current) {
            <div class="tb-team">
              <app-caseflow-agent-live-view [graph]="current" (graphChange)="graph.set($event)" />
              <h3 class="tb-section tb-section--small">Weiter konfigurieren</h3>
              <p class="tb-hint">Alle drei öffnen dasselbe Team — nur mit mehr Stellschrauben.</p>
              <ul class="tb-links">
                <li>
                  <a routerLink="/process-designer">Prozess-Designer</a>
                  <span>Schritte, Bedingungen und Schleifen im Detail.</span>
                </li>
                <li>
                  <a routerLink="/caseflow/studio">CaseFlow Studio</a>
                  <span>Szenarien fahren und Läufe beobachten.</span>
                </li>
                <li>
                  <a routerLink="/codehug/internals">VP-Editor unter CodeHug</a>
                  <span>Die technische Sicht auf denselben Graphen.</span>
                </li>
              </ul>
            </div>
          }
        }
      }
    </section>
  `,
})
export class CaseFlowTeamBuilderComponent {
  private readonly builder = inject(CaseFlowTeamBuilderService);
  private readonly visualProcess = inject(VisualProcessApiService);

  protected readonly teamKind = TEAM_TEMPLATE_KIND_TEAM;

  protected readonly phase = signal<Phase>('gallery');
  protected readonly search = signal('');
  protected readonly loading = signal(true);
  protected readonly saving = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly roleToAdd = signal('');

  private readonly templates = signal<readonly TeamTemplate[]>([]);
  private readonly savedTeams = signal<readonly SavedGraphSummary[]>([]);
  protected readonly roles = signal<readonly CatalogRole[]>([]);
  protected readonly draft = signal<TeamDraft | null>(null);
  protected readonly graph = signal<VpGraph | null>(null);

  /** Small teams first: that is the size this view is for. */
  protected readonly visibleTemplates = computed(() => {
    const needle = this.search().trim().toLocaleLowerCase();
    const matching = this.templates().filter(template => this.matches(template, needle));
    return [...matching].sort((left, right) => Number(isSmallTeam(right)) - Number(isSmallTeam(left)));
  });

  protected readonly visibleTeams = computed(() => {
    const needle = this.search().trim().toLocaleLowerCase();
    return this.savedTeams().filter(
      team => !needle || `${team.name} ${team.description}`.toLocaleLowerCase().includes(needle),
    );
  });

  private readonly issues = computed(() => {
    const current = this.draft();
    return current ? validateDraft(current) : [];
  });

  protected readonly teamIssues = computed(() =>
    this.issues()
      .filter(issue => !issue.step_id)
      .map(issue => issue.message),
  );

  protected readonly canSave = computed(() => this.draft() !== null && this.issues().length === 0);

  constructor() {
    this.load();
  }

  protected issueFor(stepId: string): string | null {
    return this.issues().find(issue => issue.step_id === stepId)?.message ?? null;
  }

  protected chooseTemplate(template: TeamTemplate): void {
    this.error.set(null);
    this.roleToAdd.set('');
    this.draft.set(draftFromTemplate(template));
    this.phase.set('draft');
  }

  protected renameTeam(name: string): void {
    const current = this.draft();
    if (current) this.draft.set({ ...current, team_name: name });
  }

  protected renameAgent(stepId: string, name: string): void {
    const current = this.draft();
    if (current) this.draft.set(withRenamedAgent(current, stepId, name));
  }

  protected removeAgent(stepId: string): void {
    const current = this.draft();
    if (current) this.draft.set(withRemovedAgent(current, stepId));
  }

  protected addAgent(): void {
    const current = this.draft();
    const role = this.roles().find(candidate => candidate.id === this.roleToAdd());
    if (!current || !role) return;
    this.draft.set(withAddedAgent(current, { role_id: role.id, display_name: role.name }));
    this.roleToAdd.set('');
  }

  protected save(): void {
    const current = this.draft();
    if (!current || !this.canSave()) return;
    this.saving.set(true);
    this.error.set(null);
    const graph = buildGraph(current, this.graphId()) as unknown as VpGraph;
    this.visualProcess.saveGraph(graph).subscribe({
      next: () => {
        this.saving.set(false);
        this.graph.set(graph);
        this.phase.set('team');
        this.refreshTeams();
      },
      error: () => {
        this.saving.set(false);
        this.error.set('Das Team konnte nicht angelegt werden. Bitte noch einmal versuchen.');
      },
    });
  }

  protected openTeam(graphId: string): void {
    this.error.set(null);
    this.visualProcess.loadSavedGraph(graphId).subscribe({
      next: graph => {
        this.graph.set(graph);
        this.phase.set('team');
      },
      error: () => this.error.set('Dieses Team konnte nicht geladen werden.'),
    });
  }

  protected backToGallery(): void {
    this.error.set(null);
    this.phase.set('gallery');
  }

  private load(): void {
    this.builder.listTemplates().subscribe({
      next: templates => {
        this.templates.set(templates);
        this.loading.set(false);
      },
      error: () => {
        this.loading.set(false);
        this.error.set('Die Vorlagen konnten nicht geladen werden.');
      },
    });
    // Roles and saved teams each fail on their own: neither is worth
    // withholding the gallery for.
    this.builder.listRoles().subscribe({ next: roles => this.roles.set(roles), error: () => this.roles.set([]) });
    this.refreshTeams();
  }

  private refreshTeams(): void {
    this.visualProcess.listSavedGraphs().subscribe({
      next: teams => this.savedTeams.set(teams ?? []),
      error: () => this.savedTeams.set([]),
    });
  }

  private matches(template: TeamTemplate, needle: string): boolean {
    if (!needle) return true;
    const haystack = [
      template.display_name,
      template.description,
      ...template.aliases,
      ...template.roles.map(role => role.display_name),
    ]
      .join(' ')
      .toLocaleLowerCase();
    return haystack.includes(needle);
  }

  private graphId(): string {
    const random = globalThis.crypto?.randomUUID?.();
    return `team-${random ?? Date.now().toString(36)}`;
  }
}
