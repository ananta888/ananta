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
import { ProjectContextService } from '../../services/project-context.service';
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

          <details class="provisioning">
            <summary>Neue serverseitige Quelle vorbereiten</summary>
            <div class="embedded-grid">
              <app-public-git-remote-onboarding
                [projectId]="projectId()"
                (remoteCreated)="onRemoteCreated($event)"
              />
              <app-workspace-registration
                [projectId]="projectId()"
                (workspaceCreated)="onWorkspaceCreated($event)"
              />
            </div>
            <section class="private-provider" aria-labelledby="private-provider-title">
              <h3 id="private-provider-title">Private Git-Provider</h3>
              @if (privateHealthLoading()) {
                <p class="notice" role="status">Provider-Health wird geprüft.</p>
              } @else if (providerAccess().privateGit) {
                <app-git-authorization-onboarding (provisioned)="reload()" />
              } @else {
                <p class="notice warning" role="status" data-testid="private-provider-gated">
                  Private Provider bleiben gesperrt, bis der Hub einen bereiten Autorisierungs-Connector meldet.
                </p>
              }
            </section>
          </details>
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
              <button type="button" (click)="detail.refresh()" [disabled]="!detail.can('refresh') || detail.mutationLoading()">Quelle aktualisieren</button>
              <button type="button" data-testid="journey-scan" (click)="detail.scan()" [disabled]="!detail.can('scan') || detail.mutationLoading()">Sicheren Scan starten</button>
            </div>
            <label for="journey-profile">Indexprofil</label>
            <select id="journey-profile" [ngModel]="profileId()" (ngModelChange)="profileId.set($event)">
              <option value="">Auswählen</option>
              @for (profile of profiles(); track profile.profileId) {
                <option [value]="profile.profileId">{{ profile.label }}{{ profile.isDefault ? ' · Standard' : '' }}</option>
              }
            </select>
            <button type="button" data-testid="journey-start-index" (click)="startIndex()" [disabled]="!canStartIndex()">Indexlauf starten</button>
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
    .provisioning { border-top: 1px solid var(--border, #9aa8a5); padding-top: .75rem; }
    .provisioning summary { cursor: pointer; font-weight: 750; }
    .embedded-grid { grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); margin-top: .9rem; }
    .private-provider { display: grid; gap: .6rem; margin-top: .9rem; }
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
    @media (max-width: 680px) { .progress { grid-template-columns: 1fr 1fr; } .form-grid { grid-template-columns: 1fr; } }
  `],
})
export class SourceIndexJourneyComponent {
  private readonly projectContext = inject(ProjectContextService);
  private readonly api = inject(SourceControlV1ApiClient);
  private readonly governanceApi = inject(SourceControlV1GovernanceApiClient);
  private readonly catalog = inject(SourceConnectorCatalogService);
  private readonly destroyRef = inject(DestroyRef);
  readonly detail = inject(SourceDetailFacade);

  private loadedProjectId = '';
  private validatedIntentFingerprint = '';
  readonly projectId = computed(() => String(this.projectContext.selectedProjectId() || '').trim());
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
  }
}
