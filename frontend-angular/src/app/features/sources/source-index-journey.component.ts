import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { finalize, forkJoin } from 'rxjs';

import type {
  SourceControlConnectionCreation,
  SourceControlConnectionValidation,
} from '../../models/source-control-v1-api.model';
import type { SourceControlGitAuthorizationHealth } from '../../models/source-control-v1-governance.model';
import type {
  SourceControlIndexAccessPreparation,
  SourceControlIndexAccessResult,
} from '../../models/source-control-index-access.model';
import { ProjectContextService } from '../../services/project-context.service';
import { SourceControlIndexAccessApiClient } from '../../services/source-control-index-access-api.client';
import {
  SourceControlConnectionIntent,
  SourceControlV1ApiClient,
} from '../../services/source-control-v1-api.client';
import { SourceControlV1GovernanceApiClient } from '../../services/source-control-v1-governance-api.client';
import { GitAuthorizationOnboardingComponent } from './git-authorization-onboarding.component';
import { PublicGitRemoteOnboardingComponent } from './public-git-remote-onboarding.component';
import {
  SourceConnectorCatalogService,
  SourceIndexProfileOption,
  SourceRemoteOption,
  SourceWorkspaceOption,
} from './source-connector-catalog.service';
import { SourceDetailFacade } from './source-detail.facade';
import { WorkspaceRegistrationComponent } from './workspace-registration.component';
import { WorkspaceSnapshotUploadComponent } from './workspace-snapshot-upload.component';

interface JourneyConnection {
  readonly id: string;
  readonly name: string;
  readonly type: string;
  readonly projectId: string;
}

type JourneyStage = 'choose' | 'validate' | 'scan' | 'observe';
type JourneyConnectionKind = 'workspace' | 'remote';

@Component({
  selector: 'app-source-index-journey',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    RouterLink,
    GitAuthorizationOnboardingComponent,
    PublicGitRemoteOnboardingComponent,
    WorkspaceRegistrationComponent,
    WorkspaceSnapshotUploadComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <main class="journey" aria-labelledby="source-index-journey-title">
      <a routerLink="/sources" queryParamsHandling="preserve" class="back-link">← Quellenübersicht</a>
      <header class="hero">
        <p class="eyebrow">Source Control v1 · geführter Ablauf</p>
        <h1 id="source-index-journey-title">Von der Quelle zum aktiven Index</h1>
        <p>Jeder Schritt arbeitet auf dem global gewählten Projekt und auf Hub-gelieferten IDs, Aktionen und ETags.</p>
      </header>

      @if (projectId()) {
        <section class="project-binding" role="status" data-testid="journey-project-binding">
          <strong>Wird Projekt {{ projectName() }} zugeordnet</strong>
          <span>Jede neue Connection gehört ausschließlich zu diesem Projekt. Ein Projektwechsel verwirft den aktuellen Entwurf.</span>
        </section>
      }

      <ol class="progress" aria-label="Fortschritt">
        <li [class.current]="stage() === 'choose'">1 Quelle</li>
        <li [class.current]="stage() === 'validate'">2 Validieren</li>
        <li [class.current]="stage() === 'scan'">3 Scan & Index</li>
        <li [class.current]="stage() === 'observe'">4 Aktivieren</li>
      </ol>

      @if (!projectId()) {
        <section class="notice error" role="alert" data-testid="journey-project-required">
          Ohne aktiven Projektkontext bleibt die Index-Journey gesperrt.
        </section>
      } @else {
        <section class="panel" aria-labelledby="journey-source-title">
          <div class="panel-head">
            <div>
              <p class="step">Schritt 1</p>
              <h2 id="journey-source-title">Quelle wählen oder registrieren</h2>
            </div>
            <button type="button" (click)="reload()" [disabled]="loading()">Hub-Stand neu laden</button>
          </div>

          @if (connections().length > 0) {
            <div class="source-list" role="list">
              @for (connection of connections(); track connection.id) {
                <button
                  type="button"
                  role="listitem"
                  [class.selected]="selectedConnectionId() === connection.id"
                  (click)="chooseExisting(connection.id)"
                >
                  <strong>{{ connection.name }}</strong>
                  <span>{{ connection.type }} · {{ connection.id }}</span>
                </button>
              }
            </div>
          } @else if (!loading()) {
            <p class="notice">In diesem Projekt ist noch keine Verbindung vorhanden.</p>
          }

          <div class="source-cards" aria-label="Neue Quelle hinzufügen">
            <details class="source-card" data-testid="source-card-public-git">
              <summary>Öffentliches Git/GitHub-Repository</summary>
              <p>Öffentliche HTTPS-Repository-URL plus Branch, Tag oder Commit. Der Hub löst unveränderlich auf.</p>
              <app-public-git-remote-onboarding
                [projectId]="projectId()"
                (remoteCreated)="onRemoteCreated($event)"
              />
            </details>

            <details class="source-card" data-testid="source-card-local-folder">
              <summary>Lokaler Ordner / lokale Git-Arbeitskopie</summary>
              <p>Sicherer Browser-Snapshot: Nur Dateien und relative Pfade werden übertragen; <code>.git</code> und andere Steuerverzeichnisse bleiben ausgeschlossen.</p>
              <app-workspace-snapshot-upload (workspaceCreated)="onWorkspaceCreated($event)" />
            </details>

            <details class="source-card" data-testid="source-card-server-workspace">
              <summary>Server-Workspace</summary>
              <p>Es werden ausschließlich vom Hub freigegebene Workspace-Labels angeboten. Freie Hostpfade sind nicht möglich.</p>
              <app-workspace-registration
                [projectId]="projectId()"
                (workspaceCreated)="onWorkspaceCreated($event)"
              />
            </details>

            <details class="source-card" data-testid="source-card-private-github">
              <summary>Privates GitHub</summary>
              @if (privateHealthLoading()) {
                <p class="notice" role="status">Providerstatus wird geprüft.</p>
              } @else if (providerAccess().privateGit) {
                <p class="notice success" role="status">Providerstatus: bereit. Zugangsdaten bleiben im Hub.</p>
                <app-git-authorization-onboarding (provisioned)="reload()" />
              } @else {
                <p class="notice warning" role="status" data-testid="private-provider-gated">
                  Providerstatus: {{ privateHealth()?.status || 'unavailable' }}.
                  Private GitHub-Repositories benötigen eine konfigurierte GitHub App oder OAuth-Installation im Hub.
                </p>
              }
            </details>
          </div>
        </section>

        <section class="panel" aria-labelledby="journey-validation-title">
          <p class="step">Schritt 2</p>
          <h2 id="journey-validation-title">Verbindung validieren und erstellen</h2>
          <div class="form-grid">
            <label for="journey-kind">Registrierung</label>
            <select id="journey-kind" [ngModel]="connectionKind()" (ngModelChange)="setConnectionKind($event)">
              <option value="workspace">Workspace</option>
              <option value="remote">Git-Remote</option>
            </select>

            @if (connectionKind() === 'workspace') {
              <label for="journey-workspace">Workspace</label>
              <select id="journey-workspace" [ngModel]="workspaceId()" (ngModelChange)="setWorkspaceId($event)">
                <option value="">Auswählen</option>
                @for (workspace of workspaces(); track workspace.workspaceId) {
                  <option [value]="workspace.workspaceId" [disabled]="!workspace.enabled">{{ workspace.label }}</option>
                }
              </select>
              <label for="journey-relative-path">Relativer Pfad</label>
              <input id="journey-relative-path" [ngModel]="relativePath()" (ngModelChange)="setRelativePath($event)" />
            } @else {
              <label for="journey-remote">Remote</label>
              <select id="journey-remote" [ngModel]="remoteId()" (ngModelChange)="setRemoteId($event)">
                <option value="">Auswählen</option>
                @for (remote of remotes(); track remote.remoteId) {
                  <option [value]="remote.remoteId" [disabled]="!remote.active">{{ remote.label }}</option>
                }
              </select>
            }

            <label for="journey-name">Anzeigename</label>
            <input id="journey-name" [ngModel]="displayName()" (ngModelChange)="setDisplayName($event)" />
          </div>
          <div class="row-actions">
            <button type="button" data-testid="journey-validate" (click)="validateDraft()" [disabled]="!canValidate()">
              {{ validating() ? 'Hub validiert...' : 'Dry-run validieren' }}
            </button>
            <button type="button" data-testid="journey-create" (click)="createValidated()" [disabled]="!canCreate()">
              {{ creating() ? 'Hub erstellt...' : 'Validierte Verbindung erstellen' }}
            </button>
          </div>
          @if (validation()) {
            <p class="notice success" role="status">Der Hub hat den aktuellen Verbindungsentwurf validiert.</p>
          }
        </section>

        <section class="panel" aria-labelledby="journey-index-title">
          <p class="step">Schritt 3</p>
          <h2 id="journey-index-title">Scannen und Indexprofil starten</h2>
          @if (!selectedConnectionId()) {
            <p class="notice">Zuerst eine bestehende oder neu erstellte Quelle wählen.</p>
          } @else {
            <div class="row-actions">
              <button type="button" (click)="refreshSource()" [disabled]="!detail.can('refresh') || detail.mutationLoading()">Quelle aktualisieren</button>
              <button type="button" data-testid="journey-scan" (click)="scanSource()" [disabled]="!detail.can('scan') || detail.mutationLoading()">Sicheren Scan starten</button>
            </div>
            @if (detail.mutationError(); as mutationError) {
              <p class="notice warning" role="alert">{{ mutationError.message }}</p>
            }

            <section class="access-card" aria-labelledby="index-access-title">
              <div class="panel-head">
                <div>
                  <h3 id="index-access-title">Indexzugriff freigeben</h3>
                  <p>Der Hub bündelt Policy-Prüfung, Aktivierung und den einmaligen Grant in einem Command.</p>
                </div>
                <button
                  type="button"
                  data-testid="prepare-index-access"
                  (click)="prepareIndexAccess()"
                  [disabled]="!detail.can('grant') || accessLoading() || accessGranting()"
                >
                  {{ accessLoading() ? 'Freigabe wird geprüft...' : 'Freigabeoptionen laden' }}
                </button>
              </div>

              @if (!detail.can('grant') && !accessReady()) {
                <p class="notice">Nächster Schritt: Quelle aktualisieren und den sicheren Scan vollständig abschließen.</p>
              } @else if (!accessPreparation() && !accessLoading()) {
                <p class="notice">Der Scan ist bereit. Jetzt serverseitige lokale Freigabeoptionen laden.</p>
              }

              @if (accessPreparation(); as preparation) {
                @if (!preparation.readiness.ready) {
                  <p class="notice warning" role="status">
                    Die Freigabe ist noch nicht bereit: {{ readinessMessage(preparation.readiness.reason_codes) }}
                  </p>
                } @else if (preparation.destinations.length === 0 || preparation.options.length === 0) {
                  <p class="notice warning" role="status">Der Hub liefert derzeit kein sicheres lokales Ziel oder keine redigierte Einmal-Option.</p>
                } @else {
                  <div class="form-grid access-fields">
                    <label for="index-access-destination">Lokales Ziel</label>
                    <select
                      id="index-access-destination"
                      data-testid="index-access-destination"
                      [ngModel]="accessDestinationId()"
                      (ngModelChange)="selectAccessDestination($event)"
                      [disabled]="accessGranting() || accessReady()"
                    >
                      @for (destination of preparation.destinations; track destination.destination_id) {
                        <option [value]="destination.destination_id">
                          {{ destination.worker_id }} · {{ destination.runtime_kind }} · {{ destination.data_residency }}
                        </option>
                      }
                    </select>

                    <label for="index-access-option">Freigabewirkung</label>
                    <select
                      id="index-access-option"
                      data-testid="index-access-option"
                      [ngModel]="accessOptionId()"
                      (ngModelChange)="selectAccessOption($event)"
                      [disabled]="accessGranting() || accessReady()"
                    >
                      @for (option of preparation.options; track option.option_id) {
                        <option [value]="option.option_id">{{ option.label }}</option>
                      }
                    </select>

                    <label for="index-access-duration">Dauer in Sekunden</label>
                    <input
                      id="index-access-duration"
                      type="number"
                      [min]="selectedAccessOption()?.duration_seconds?.minimum || 60"
                      [max]="selectedAccessOption()?.duration_seconds?.maximum || 900"
                      [ngModel]="accessDurationSeconds()"
                      (ngModelChange)="setAccessDuration($event)"
                      [disabled]="accessGranting() || accessReady()"
                    />
                  </div>

                  @if (selectedAccessOption(); as option) {
                    <div class="effect-summary" data-testid="index-access-effect">
                      <strong>Genaue Wirkung: lokal, redigiert und einmalig</strong>
                      <ul>
                        <li>Zielort: <code>{{ option.effect.provider_location }}</code> – keine Cloud- oder externe Freigabe.</li>
                        <li>Transformation: <code>{{ option.effect.transformation }}</code> – nur redigierter Indexkontext.</li>
                        <li>Gültigkeit: <code>{{ option.effect.one_time ? 'one-time' : 'unzulässig' }}</code> – kein dauerhafter Zugriff.</li>
                      </ul>
                    </div>
                  }

                  <label class="confirmation" for="index-access-confirmation">
                    <input
                      id="index-access-confirmation"
                      data-testid="index-access-confirmation"
                      type="checkbox"
                      [ngModel]="accessConfirmed()"
                      (ngModelChange)="accessConfirmed.set($event === true)"
                      [disabled]="accessGranting() || accessReady()"
                    />
                    Ich bestätige die einmalige, ausschließlich lokale und redigierte Freigabe für diesen Indexlauf.
                  </label>
                  <button
                    type="button"
                    data-testid="grant-index-access"
                    (click)="grantIndexAccess()"
                    [disabled]="!canGrantIndexAccess()"
                  >
                    {{ accessGranting() ? 'Hub gibt Zugriff frei...' : 'Indexzugriff freigeben' }}
                  </button>
                }
              }

              @if (accessError()) {
                <p class="notice error" role="alert">{{ accessError() }}</p>
              }
              @if (accessReady()) {
                <p class="notice success" role="status" data-testid="index-access-success">
                  Indexzugriff ist freigegeben. Der Indexlauf kann jetzt gestartet werden.
                </p>
              }
            </section>

            <label for="journey-profile">Indexprofil</label>
            <select id="journey-profile" [ngModel]="profileId()" (ngModelChange)="profileId.set($event)">
              <option value="">Auswählen</option>
              @for (profile of profiles(); track profile.profileId) {
                <option [value]="profile.profileId">{{ profile.label }}{{ profile.isDefault ? ' · Standard' : '' }}</option>
              }
            </select>
            <button type="button" data-testid="journey-start-index" (click)="startIndex()" [disabled]="!canStartIndex()">Indexlauf starten</button>
            @if (!accessReady()) {
              <p class="notice">Der Indexlauf bleibt gesperrt, bis <strong>Indexzugriff freigeben</strong> erfolgreich abgeschlossen ist.</p>
            }
          }
        </section>

        <section class="panel" aria-labelledby="journey-runs-title">
          <div class="panel-head">
            <div>
              <p class="step">Schritt 4</p>
              <h2 id="journey-runs-title">Läufe beobachten, aktivieren oder zurückrollen</h2>
            </div>
            <button type="button" (click)="reloadDetail()" [disabled]="!selectedConnectionId() || detail.loading()">Läufe aktualisieren</button>
          </div>
          @if (detail.runs().length === 0) {
            <p class="notice">Noch kein serverseitiger Indexlauf vorhanden.</p>
          } @else {
            <div class="run-list">
              @for (run of detail.runs(); track run.indexId) {
                <article>
                  <div><strong>{{ run.indexId }}</strong><span>{{ run.status }} · Abdeckung {{ run.coveragePercent ?? 'n/a' }}%</span></div>
                  <div class="row-actions">
                    <button type="button" (click)="detail.activateIndex(run.indexId)" [disabled]="!detail.can('activate') || detail.mutationLoading()">Aktivieren</button>
                    <button type="button" (click)="detail.rollbackIndex(run.indexId)" [disabled]="!detail.can('rollback') || detail.mutationLoading()">Rollback</button>
                  </div>
                </article>
              }
            </div>
          }
        </section>

        @if (errorMessage()) {
          <p class="notice error sticky" role="alert">{{ errorMessage() }}</p>
        }
        @if (detail.sourceError(); as error) {
          <p class="notice error sticky" role="alert">{{ error.message }}</p>
        }
        @if (detail.lifecycleMessage()) {
          <p class="notice success sticky" role="status">{{ detail.lifecycleMessage() }}</p>
        }
      }
    </main>
  `,
  styles: [`
    :host { display: block; }
    .journey { display: grid; gap: 1.1rem; max-width: 1120px; margin: 0 auto; }
    .back-link { width: fit-content; }
    .hero { padding: clamp(1.2rem, 4vw, 2.4rem); border-radius: 1rem; color: #f7fbf8; background: radial-gradient(circle at 88% 15%, #db9d35 0 8%, transparent 9%), linear-gradient(125deg, #0a5549, #123a44); box-shadow: 0 18px 50px rgb(10 55 50 / 22%); }
    .hero h1, .hero p, .panel h2, .panel h3, .step, .eyebrow { margin: 0; }
    .hero h1 { max-width: 760px; font-family: Georgia, 'Times New Roman', serif; font-size: clamp(2rem, 5vw, 4rem); line-height: .98; }
    .hero > p:last-child { max-width: 720px; margin-top: 1rem; }
    .eyebrow, .step { font-size: .72rem; font-weight: 750; letter-spacing: .13em; text-transform: uppercase; }
    .progress { display: grid; grid-template-columns: repeat(4, 1fr); gap: .45rem; margin: 0; padding: 0; list-style: none; }
    .progress li { padding: .65rem; border-bottom: 3px solid var(--border, #9aa8a5); color: var(--muted, #52615f); }
    .progress li.current { border-color: #db7c28; color: var(--fg, #17211f); font-weight: 750; }
    .panel { display: grid; gap: .85rem; padding: clamp(1rem, 3vw, 1.5rem); border: 1px solid var(--border, #9aa8a5); border-radius: .85rem; background: color-mix(in srgb, var(--card-bg, #fff) 95%, #dcefe8); }
    .panel-head, .row-actions, .run-list article { display: flex; align-items: center; justify-content: space-between; gap: .7rem; flex-wrap: wrap; }
    .source-list, .run-list, .embedded-grid { display: grid; gap: .7rem; }
    .source-list { grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); }
    .source-list button { display: grid; gap: .3rem; text-align: left; }
    .source-list button.selected { outline: 3px solid #db7c28; outline-offset: 2px; }
    .source-list span, .run-list span { display: block; color: var(--muted, #52615f); font-size: .82rem; overflow-wrap: anywhere; }
    .project-binding { display: grid; gap: .25rem; padding: .85rem 1rem; border-left: 5px solid #db7c28; border-radius: .55rem; background: #fff4dc; color: #51330d; }
    .source-cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .8rem; }
    .source-card { align-self: start; padding: .85rem; border: 1px solid var(--border, #9aa8a5); border-radius: .7rem; background: var(--card-bg, #fff); }
    .source-card summary { cursor: pointer; font-weight: 800; font-size: 1.02rem; }
    .source-card > p { color: var(--muted, #52615f); }
    .access-card { display: grid; gap: .8rem; padding: 1rem; border: 2px solid #176b5b; border-radius: .75rem; background: color-mix(in srgb, var(--card-bg, #fff) 92%, #bfe8dc); }
    .access-card h3, .access-card p { margin: 0; }
    .effect-summary { padding: .8rem; border-radius: .55rem; background: #e4f5ed; color: #123f33; }
    .effect-summary ul { margin-bottom: 0; }
    .confirmation { display: flex; align-items: flex-start; gap: .65rem; font-weight: 700; }
    .confirmation input { width: auto; min-height: 1.25rem; margin-top: .12rem; }
    .form-grid { display: grid; grid-template-columns: minmax(10rem, .65fr) minmax(14rem, 1.35fr); gap: .65rem .85rem; align-items: center; }
    input, select, button { min-height: 2.4rem; padding: .48rem .7rem; font: inherit; }
    input, select { width: 100%; border: 1px solid var(--border, #9aa8a5); border-radius: .45rem; background: var(--bg, #fff); color: var(--fg, #17211f); }
    button { border: 1px solid #175e51; border-radius: .45rem; color: #f8fffc; background: #175e51; cursor: pointer; }
    button:disabled { opacity: .45; cursor: not-allowed; }
    .run-list article { padding: .8rem; border: 1px solid var(--border, #9aa8a5); border-radius: .6rem; }
    .notice { margin: 0; padding: .7rem; background: #edf4f1; color: #183b34; }
    .warning { background: #fff1d7; color: #754700; }
    .error { background: #ffe8e3; color: #8d1b12; }
    .success { background: #e2f6e9; color: #08734e; }
    .sticky { position: sticky; bottom: .5rem; box-shadow: 0 8px 26px rgb(0 0 0 / 15%); }
    @media (max-width: 680px) { .progress { grid-template-columns: 1fr 1fr; } .form-grid, .source-cards { grid-template-columns: 1fr; } }
  `],
})
export class SourceIndexJourneyComponent {
  private readonly projectContext = inject(ProjectContextService);
  private readonly api = inject(SourceControlV1ApiClient);
  private readonly governanceApi = inject(SourceControlV1GovernanceApiClient);
  private readonly indexAccessApi = inject(SourceControlIndexAccessApiClient);
  private readonly catalog = inject(SourceConnectorCatalogService);
  private readonly destroyRef = inject(DestroyRef);
  readonly detail = inject(SourceDetailFacade);

  private loadedProjectId = '';
  private validatedIntentFingerprint = '';
  readonly projectId = computed(() => String(this.projectContext.selectedProjectId() || '').trim());
  readonly projectName = computed(() => this.projectContext.selectedProject()?.name || this.projectId());
  readonly connections = signal<readonly JourneyConnection[]>([]);
  readonly workspaces = signal<readonly SourceWorkspaceOption[]>([]);
  readonly remotes = signal<readonly SourceRemoteOption[]>([]);
  readonly profiles = signal<readonly SourceIndexProfileOption[]>([]);
  readonly privateHealth = signal<SourceControlGitAuthorizationHealth | null>(null);
  readonly selectedConnectionId = signal('');
  readonly connectionKind = signal<JourneyConnectionKind>('workspace');
  readonly workspaceId = signal('');
  readonly remoteId = signal('');
  readonly relativePath = signal('');
  readonly displayName = signal('');
  readonly profileId = signal('');
  readonly validation = signal<SourceControlConnectionValidation | null>(null);
  readonly loading = signal(false);
  readonly privateHealthLoading = signal(false);
  readonly validating = signal(false);
  readonly creating = signal(false);
  readonly errorMessage = signal('');
  readonly accessPreparation = signal<SourceControlIndexAccessPreparation | null>(null);
  readonly accessResult = signal<SourceControlIndexAccessResult | null>(null);
  readonly accessDestinationId = signal('');
  readonly accessOptionId = signal('');
  readonly accessDurationSeconds = signal(900);
  readonly accessConfirmed = signal(false);
  readonly accessLoading = signal(false);
  readonly accessGranting = signal(false);
  readonly accessError = signal('');
  readonly accessReady = computed(() =>
    this.accessResult()?.access_ready === true
      && this.accessResult()?.next_actions.includes('start_index_run') === true,
  );
  readonly selectedAccessOption = computed(() =>
    this.accessPreparation()?.options.find(option => option.option_id === this.accessOptionId()) ?? null,
  );

  readonly providerAccess = computed(() => {
    const health = this.privateHealth();
    const projectAvailable = Boolean(this.projectId());
    return {
      publicGit: projectAvailable,
      workspace: projectAvailable,
      privateGit: Boolean(
        projectAvailable &&
          health &&
          health.status !== 'unavailable' &&
          (health.connector_ready.github_repository || health.connector_ready.generic_git),
      ),
    } as const;
  });

  readonly stage = computed<JourneyStage>(() => {
    if (!this.selectedConnectionId()) return this.validation() ? 'validate' : 'choose';
    return this.detail.runs().length > 0 ? 'observe' : 'scan';
  });

  readonly canValidate = computed(() =>
    Boolean(this.connectionIntent()) && !this.loading() && !this.validating() && !this.creating(),
  );

  readonly canCreate = computed(() => {
    const intent = this.connectionIntent();
    return Boolean(
      intent &&
        this.validation()?.valid &&
        this.validatedIntentFingerprint === this.intentFingerprint(intent) &&
        !this.validating() &&
        !this.creating(),
    );
  });

  readonly canStartIndex = computed(() =>
    Boolean(
      this.selectedConnectionId() &&
        this.profiles().some((profile) => profile.profileId === this.profileId()) &&
        this.detail.indexProfiles().some((profile) => profile.profileId === this.profileId()) &&
        this.detail.can('index') &&
        this.accessReady() &&
        !this.detail.mutationLoading(),
    ),
  );

  constructor() {
    effect(() => {
      const projectId = this.projectId();
      if (projectId === this.loadedProjectId) return;
      this.loadedProjectId = projectId;
      this.resetProjectState();
      if (projectId) this.loadProject(projectId);
    });
  }

  reload(): void {
    const projectId = this.projectId();
    if (projectId) this.loadProject(projectId);
  }

  chooseExisting(connectionId: string): void {
    const connection = this.connections().find((item) => item.id === connectionId);
    if (!connection || connection.projectId !== this.projectId()) {
      this.errorMessage.set('Die gewählte Quelle gehört nicht zum aktiven Projekt.');
      return;
    }
    this.errorMessage.set('');
    this.selectedConnectionId.set(connection.id);
    this.resetIndexAccess();
    this.detail.load(connection.id);
  }

  validateDraft(): void {
    const projectId = this.projectId();
    const intent = this.connectionIntent();
    if (!projectId || !intent || this.validating()) {
      this.errorMessage.set('Der Verbindungsentwurf ist unvollständig.');
      return;
    }
    const fingerprint = this.intentFingerprint(intent);
    this.validating.set(true);
    this.validation.set(null);
    this.validatedIntentFingerprint = '';
    this.errorMessage.set('');
    this.api.validateConnection(intent).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.validating.set(false)),
    ).subscribe({
      next: (validation) => {
        if (this.projectId() !== projectId) return;
        if (validation.connection.project_id !== projectId) {
          this.errorMessage.set('Die Validierungsprojektion liegt außerhalb des aktiven Projekts.');
          return;
        }
        this.validation.set(validation);
        this.validatedIntentFingerprint = fingerprint;
      },
      error: () => this.errorMessage.set('Der Hub konnte den Entwurf nicht validieren.'),
    });
  }

  createValidated(): void {
    const projectId = this.projectId();
    const intent = this.connectionIntent();
    if (!projectId || !intent || !this.canCreate()) {
      this.errorMessage.set('Vor dem Erstellen ist eine aktuelle erfolgreiche Validierung erforderlich.');
      return;
    }
    this.creating.set(true);
    this.errorMessage.set('');
    this.api.createConnection(intent, `ui:source-journey:create:${crypto.randomUUID()}`).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.creating.set(false)),
    ).subscribe({
      next: (creation) => this.acceptCreation(projectId, creation),
      error: () => this.errorMessage.set('Der Hub konnte die validierte Verbindung nicht erstellen.'),
    });
  }

  startIndex(): void {
    const profileId = this.profileId();
    if (this.canStartIndex()) this.detail.startIndex(profileId);
  }

  refreshSource(): void {
    this.resetIndexAccess();
    this.detail.refresh();
  }

  scanSource(): void {
    this.resetIndexAccess();
    this.detail.scan();
  }

  prepareIndexAccess(): void {
    const connectionId = this.selectedConnectionId();
    const projectId = this.projectId();
    if (!connectionId || !projectId || !this.detail.can('grant') || this.accessLoading()) {
      this.accessError.set('Der sichere Scan muss vor der Freigabe vollständig abgeschlossen sein.');
      return;
    }
    this.accessLoading.set(true);
    this.accessError.set('');
    this.accessResult.set(null);
    this.indexAccessApi.prepare(connectionId, projectId).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.accessLoading.set(false)),
    ).subscribe({
      next: preparation => {
        if (this.projectId() !== projectId || this.selectedConnectionId() !== connectionId) return;
        this.accessPreparation.set(preparation);
        this.accessDestinationId.set(preparation.destinations[0]?.destination_id ?? '');
        const option = preparation.options[0];
        this.accessOptionId.set(option?.option_id ?? '');
        this.accessDurationSeconds.set(option?.duration_seconds.default ?? 900);
        this.accessConfirmed.set(false);
      },
      error: () => this.accessError.set(
        'Der Hub konnte keine sichere lokale Indexfreigabe vorbereiten. Scan und Workerstatus prüfen.',
      ),
    });
  }

  selectAccessDestination(value: string): void {
    this.accessDestinationId.set(String(value || '').trim());
    this.accessConfirmed.set(false);
  }

  selectAccessOption(value: string): void {
    const optionId = String(value || '').trim();
    this.accessOptionId.set(optionId);
    const option = this.accessPreparation()?.options.find(item => item.option_id === optionId);
    if (option) this.accessDurationSeconds.set(option.duration_seconds.default);
    this.accessConfirmed.set(false);
  }

  setAccessDuration(value: unknown): void {
    this.accessDurationSeconds.set(Number(value));
    this.accessConfirmed.set(false);
  }

  readonly canGrantIndexAccess = computed(() => {
    const preparation = this.accessPreparation();
    const option = this.selectedAccessOption();
    const duration = this.accessDurationSeconds();
    return Boolean(
      preparation?.readiness.ready
      && preparation.destinations.some(item => item.destination_id === this.accessDestinationId())
      && option
      && Number.isSafeInteger(duration)
      && duration >= option.duration_seconds.minimum
      && duration <= option.duration_seconds.maximum
      && this.accessConfirmed()
      && !this.accessLoading()
      && !this.accessGranting()
      && !this.accessReady(),
    );
  });

  grantIndexAccess(): void {
    const preparation = this.accessPreparation();
    const projectId = this.projectId();
    if (!preparation || !projectId || !this.canGrantIndexAccess()) {
      this.accessError.set('Ziel, Freigabewirkung, Dauer und ausdrückliche Bestätigung sind erforderlich.');
      return;
    }
    this.accessGranting.set(true);
    this.accessError.set('');
    this.indexAccessApi.grant(
      preparation,
      projectId,
      {
        destinationId: this.accessDestinationId(),
        optionId: this.accessOptionId(),
        durationSeconds: this.accessDurationSeconds(),
        confirmed: true,
      },
      `ui:index-access:${crypto.randomUUID()}`,
    ).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.accessGranting.set(false)),
    ).subscribe({
      next: result => {
        if (this.projectId() !== projectId || result.connection_id !== this.selectedConnectionId()) return;
        this.accessResult.set(result);
        this.reloadDetail();
      },
      error: () => this.accessError.set(
        'Der Hub hat die Freigabe abgelehnt. Optionen neu laden und die Wirkung erneut bestätigen.',
      ),
    });
  }

  readinessMessage(reasonCodes: readonly string[]): string {
    return reasonCodes.length > 0
      ? reasonCodes.join(', ')
      : 'Scan, Revision oder lokales Ziel ist noch nicht bereit.';
  }

  reloadDetail(): void {
    const connectionId = this.selectedConnectionId();
    if (connectionId) this.detail.load(connectionId);
  }

  setConnectionKind(kind: JourneyConnectionKind): void {
    if (kind !== 'workspace' && kind !== 'remote') return;
    this.connectionKind.set(kind);
    this.invalidateValidation();
  }

  setWorkspaceId(value: string): void {
    this.workspaceId.set(String(value || '').trim());
    this.invalidateValidation();
  }

  setRemoteId(value: string): void {
    this.remoteId.set(String(value || '').trim());
    this.invalidateValidation();
  }

  setRelativePath(value: string): void {
    this.relativePath.set(String(value || '').trim());
    this.invalidateValidation();
  }

  setDisplayName(value: string): void {
    this.displayName.set(String(value || '').trim());
    this.invalidateValidation();
  }

  onRemoteCreated(remote: { readonly remote_id: string }): void {
    this.remoteId.set(remote.remote_id);
    this.connectionKind.set('remote');
    this.reload();
  }

  onWorkspaceCreated(workspace: { readonly workspace_id: string }): void {
    this.workspaceId.set(workspace.workspace_id);
    this.connectionKind.set('workspace');
    this.reload();
  }

  private loadProject(projectId: string): void {
    this.loading.set(true);
    this.errorMessage.set('');
    forkJoin({
      connectionPage: this.api.listConnections({ limit: 200 }),
      workspaces: this.catalog.loadWorkspaces(projectId),
      remotes: this.catalog.loadRemotes(projectId),
      profiles: this.catalog.loadIndexProfiles(projectId),
    }).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.loading.set(false)),
    ).subscribe({
      next: ({ connectionPage, workspaces, remotes, profiles }) => {
        if (this.projectId() !== projectId) return;
        const scoped = connectionPage.items.filter(
          (projection) => projection.connection.project_id === projectId,
        );
        if (scoped.length !== connectionPage.items.length) {
          this.errorMessage.set('Projektfremde Quellenprojektionen wurden verworfen.');
        }
        this.connections.set(scoped.map((projection) => ({
          id: projection.connection_id,
          name: typeof projection.connection['display_name'] === 'string'
            ? projection.connection['display_name']
            : projection.connection_id,
          type: typeof projection.connection['connector_type'] === 'string'
            ? projection.connection['connector_type']
            : 'unbekannt',
          projectId: projection.connection.project_id,
        })));
        this.workspaces.set(workspaces);
        this.remotes.set(remotes);
        this.profiles.set(profiles);
        if (!profiles.some((profile) => profile.profileId === this.profileId())) {
          this.profileId.set(profiles.find((profile) => profile.isDefault)?.profileId ?? '');
        }
      },
      error: () => this.errorMessage.set('Die Projektquellen und Indexprofile konnten nicht geladen werden.'),
    });
    this.loadPrivateProviderHealth(projectId);
  }

  private loadPrivateProviderHealth(expectedProjectId: string): void {
    this.privateHealthLoading.set(true);
    this.privateHealth.set(null);
    this.governanceApi.gitAuthorizationHealth().pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.privateHealthLoading.set(false)),
    ).subscribe({
      next: (health) => {
        if (this.projectId() === expectedProjectId) this.privateHealth.set(health);
      },
      error: () => this.privateHealth.set(null),
    });
  }

  private acceptCreation(projectId: string, creation: SourceControlConnectionCreation): void {
    if (this.projectId() !== projectId) return;
    if (creation.connection.project_id !== projectId) {
      this.errorMessage.set('Die erstellte Verbindung liegt außerhalb des aktiven Projekts.');
      return;
    }
    this.validation.set(null);
    this.validatedIntentFingerprint = '';
    this.selectedConnectionId.set(creation.connection.connection_id);
    this.resetIndexAccess();
    this.detail.load(creation.connection.connection_id);
    this.loadProject(projectId);
  }

  private connectionIntent(): SourceControlConnectionIntent | null {
    const displayName = this.displayName().trim();
    if (!displayName || displayName.length > 256) return null;
    if (this.connectionKind() === 'workspace') {
      const workspace = this.workspaces().find(
        (item) => item.workspaceId === this.workspaceId() && item.enabled,
      );
      if (!workspace) return null;
      const relativePath = this.relativePath().trim();
      return {
        connector_type: 'registered_workspace',
        workspace_id: workspace.workspaceId,
        ...(relativePath ? { relative_path: relativePath } : {}),
        display_name: displayName,
        sensitivity: 'internal',
      };
    }
    const remote = this.remotes().find(
      (item) => item.remoteId === this.remoteId() && item.active,
    );
    if (!remote) return null;
    return {
      connector_type: remote.kind,
      remote_id: remote.remoteId,
      display_name: displayName,
      sensitivity: 'internal',
    };
  }

  private intentFingerprint(intent: SourceControlConnectionIntent): string {
    return JSON.stringify(intent);
  }

  private invalidateValidation(): void {
    this.validation.set(null);
    this.validatedIntentFingerprint = '';
    this.errorMessage.set('');
  }

  private resetProjectState(): void {
    this.connections.set([]);
    this.workspaces.set([]);
    this.remotes.set([]);
    this.profiles.set([]);
    this.privateHealth.set(null);
    this.selectedConnectionId.set('');
    this.profileId.set('');
    this.validation.set(null);
    this.validatedIntentFingerprint = '';
    this.errorMessage.set('');
    this.resetIndexAccess();
  }

  private resetIndexAccess(): void {
    this.accessPreparation.set(null);
    this.accessResult.set(null);
    this.accessDestinationId.set('');
    this.accessOptionId.set('');
    this.accessDurationSeconds.set(900);
    this.accessConfirmed.set(false);
    this.accessLoading.set(false);
    this.accessGranting.set(false);
    this.accessError.set('');
  }
}
