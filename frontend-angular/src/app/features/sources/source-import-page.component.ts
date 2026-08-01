import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, ActivatedRouteSnapshot, RouterLink } from '@angular/router';
import { finalize, forkJoin, switchMap, throwError } from 'rxjs';

import { SourceControlV1GovernanceApiClient } from '../../services/source-control-v1-governance-api.client';
import type {
  SourceControlGitAuthorizationView,
  SourceControlPublicRemoteCreation,
  SourceControlWorkspaceRegistration,
} from '../../models/source-control-v1-governance.model';
import {
  SourceControlV1ApiClient,
  normalizeSourceWorkspaceRelativePath,
} from '../../services/source-control-v1-api.client';
import { GitAuthorizationOnboardingComponent } from './git-authorization-onboarding.component';
import { PublicGitRemoteOnboardingComponent } from './public-git-remote-onboarding.component';
import { WorkspaceRegistrationComponent } from './workspace-registration.component';
import {
  SourceConnectorCatalogService,
} from './source-connector-catalog.service';
import type {
  SourceConnectorCapability,
  SourceIndexProfileOption,
  SourceRemoteOption,
  SourceWorkspaceOption,
} from './source-connector-catalog.service';

type ImportKind = 'direct_text' | 'open_notebook' | 'registered_workspace' | 'registered_remote';
type Sensitivity = 'public' | 'internal' | 'confidential' | 'restricted';

interface CanonicalNotebookOutput {
  readonly output_type: 'stream' | 'text' | 'error';
  readonly text: string;
}

interface CanonicalNotebookCell {
  readonly cell_type: 'markdown' | 'code';
  readonly source: string;
  readonly outputs: readonly CanonicalNotebookOutput[];
}

interface CanonicalNotebook {
  readonly cells: readonly CanonicalNotebookCell[];
}

export function sourceProjectIdFromRoute(snapshot: ActivatedRouteSnapshot): string {
  let current: ActivatedRouteSnapshot | null = snapshot;
  while (current) {
    const dataProjectId = String(
      current.data['projectId'] ??
        (typeof current.data['project'] === 'object' &&
        current.data['project'] !== null &&
        'id' in current.data['project']
          ? (current.data['project'] as { id?: unknown }).id
          : ''),
    ).trim();
    if (dataProjectId) {
      return dataProjectId;
    }

    const routeProjectId = String(current.paramMap.get('projectId') ?? '').trim();
    if (routeProjectId) {
      return routeProjectId;
    }

    const queryProjectId = String(
      current.queryParamMap.get('projectId') ?? current.queryParamMap.get('project_id') ?? '',
    ).trim();
    if (queryProjectId) {
      return queryProjectId;
    }
    current = current.parent;
  }
  return '';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function canonicalNotebook(value: unknown): CanonicalNotebook | null {
  if (!isRecord(value) || Object.keys(value).some((key) => key !== 'cells')) {
    return null;
  }
  if (!Array.isArray(value['cells'])) {
    return null;
  }

  const cells: CanonicalNotebookCell[] = [];
  for (const candidate of value['cells']) {
    if (
      !isRecord(candidate) ||
      Object.keys(candidate).some(
        (key) => key !== 'cell_type' && key !== 'source' && key !== 'outputs',
      )
    ) {
      return null;
    }
    const cellType = candidate['cell_type'];
    const source = candidate['source'];
    const rawOutputs = candidate['outputs'];
    if (
      (cellType !== 'markdown' && cellType !== 'code') ||
      typeof source !== 'string' ||
      !Array.isArray(rawOutputs)
    ) {
      return null;
    }

    const outputs: CanonicalNotebookOutput[] = [];
    for (const output of rawOutputs) {
      if (
        !isRecord(output) ||
        Object.keys(output).some((key) => key !== 'output_type' && key !== 'text')
      ) {
        return null;
      }
      const outputType = output['output_type'];
      const text = output['text'];
      if (
        (outputType !== 'stream' && outputType !== 'text' && outputType !== 'error') ||
        typeof text !== 'string'
      ) {
        return null;
      }
      outputs.push({ output_type: outputType, text });
    }
    cells.push({ cell_type: cellType, source, outputs });
  }
  return { cells };
}

@Component({
  selector: 'app-source-import-page',
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
    <main class="import-shell" aria-labelledby="source-import-title">
      <a class="back-link" routerLink="/sources">Zur Source-Control-Uebersicht</a>
      <header>
        <p class="eyebrow">Source Control v1</p>
        <h1 id="source-import-title">Quelle aufnehmen</h1>
        <p>
          Inhalte werden vor dem Speichern vom Hub validiert. Identitaeten, Digests und
          Revisionen werden ausschließlich serverseitig erzeugt.
        </p>
      </header>

      @if (!projectId) {
        <section class="notice error" role="alert" data-testid="source-project-required">
          Kein Projektkontext vorhanden. Die Aufnahme bleibt fail-closed.
        </section>
      }

      <section class="source-grid" aria-label="Quelltyp">
        @for (capability of catalog.capabilities; track capability.kind) {
          <button
            type="button"
            class="source-card"
            [class.selected]="selectedKind() === capability.kind"
            [attr.aria-pressed]="selectedKind() === capability.kind"
            [attr.aria-disabled]="!capability.available"
            [disabled]="!capability.available"
            (click)="selectKind(capability)"
          >
            <strong>{{ capability.label }}</strong>
            <span>{{ capability.description }}</span>
            @if (!capability.available || !capability.persistable) {
              <small>{{ capability.reason }}</small>
            }
          </button>
        }
      </section>

      @if (selectedKind() === 'direct_text') {
        <section class="editor" aria-labelledby="direct-text-title">
          <h2 id="direct-text-title">Direkttext</h2>
          <label for="direct-name">Anzeigename</label>
          <input
            id="direct-name"
            data-testid="direct-display-name"
            [ngModel]="displayName()"
            (ngModelChange)="displayName.set($event)"
          />
          <label for="direct-media-type">Medientyp</label>
          <select
            id="direct-media-type"
            [ngModel]="mediaType()"
            (ngModelChange)="mediaType.set($event)"
          >
            <option value="text/plain">Text</option>
            <option value="text/markdown">Markdown</option>
          </select>
          <label for="direct-sensitivity">Sensitivitaet</label>
          <select
            id="direct-sensitivity"
            [ngModel]="sensitivity()"
            (ngModelChange)="sensitivity.set($event)"
          >
            <option value="public">Public</option>
            <option value="internal">Internal</option>
            <option value="confidential">Confidential</option>
            <option value="restricted">Restricted</option>
          </select>
          <label for="direct-content">Inhalt</label>
          <textarea
            id="direct-content"
            data-testid="direct-content"
            rows="12"
            [ngModel]="directContent()"
            (ngModelChange)="directContent.set($event)"
          ></textarea>
          <p class="hint">Der Hub prueft Inhalt und Metadaten vor der Aufnahme.</p>
        </section>
      }

      @if (selectedKind() === 'open_notebook') {
        <section class="editor" aria-labelledby="notebook-title">
          <h2 id="notebook-title">Notebook</h2>
          <label for="notebook-name">Anzeigename</label>
          <input
            id="notebook-name"
            data-testid="notebook-display-name"
            [ngModel]="displayName()"
            (ngModelChange)="displayName.set($event)"
          />
          <label for="notebook-sensitivity">Sensitivitaet</label>
          <select
            id="notebook-sensitivity"
            [ngModel]="sensitivity()"
            (ngModelChange)="sensitivity.set($event)"
          >
            <option value="public">Public</option>
            <option value="internal">Internal</option>
            <option value="confidential">Confidential</option>
            <option value="restricted">Restricted</option>
          </select>
          <label for="notebook-json">Kanonisches Notebook-JSON</label>
          <textarea
            id="notebook-json"
            data-testid="notebook-json"
            rows="14"
            [ngModel]="notebookJson()"
            (ngModelChange)="notebookJson.set($event)"
          ></textarea>
          <p class="hint">
            Erlaubt ist exakt <code>cells</code> mit Markdown-/Code-Zellen und kanonischen
            Ausgaben. Fremde Jupyter-Metadaten werden nicht stillschweigend umgeschrieben.
          </p>
        </section>
      }

      @if (selectedKind() === 'registered_workspace') {
        <section class="editor" aria-labelledby="workspace-title">
          <h2 id="workspace-title">Registrierte Workspaces</h2>
          <app-workspace-registration
            [projectId]="projectId"
            (workspaceCreated)="onWorkspaceRegistered($event)"
          />
          <label for="workspace-name">Anzeigename</label>
          <input
            id="workspace-name"
            data-testid="workspace-display-name"
            [ngModel]="displayName()"
            (ngModelChange)="displayName.set($event)"
          />
          <label for="workspace-sensitivity">Sensitivitaet</label>
          <select
            id="workspace-sensitivity"
            [ngModel]="sensitivity()"
            (ngModelChange)="sensitivity.set($event)"
          >
            <option value="public">Public</option>
            <option value="internal">Internal</option>
            <option value="confidential">Confidential</option>
            <option value="restricted">Restricted</option>
          </select>
          <label for="workspace-option">Workspace</label>
          <select
            id="workspace-option"
            data-testid="workspace-catalog"
            [ngModel]="selectedWorkspaceId()"
            (ngModelChange)="selectedWorkspaceId.set($event)"
          >
            <option value="">Auswaehlen</option>
            @for (workspace of workspaces(); track workspace.workspaceId) {
              <option [value]="workspace.workspaceId" [disabled]="!workspace.enabled">
                {{ workspace.label }}{{ workspace.readOnly ? ' (read-only)' : '' }}
              </option>
            }
          </select>
          <label for="workspace-relative-path">Optionaler relativer Pfad</label>
          <input
            id="workspace-relative-path"
            data-testid="workspace-relative-path"
            placeholder="src/app"
            [ngModel]="workspaceRelativePath()"
            (ngModelChange)="workspaceRelativePath.set($event)"
          />
          <p class="notice" role="status">
            Gesendet werden nur die serverregistrierte Workspace-ID und optional ein
            validierter relativer Pfad. Rohe Pfade und Connection-Identity bleiben
            Hub-Verantwortung.
          </p>
        </section>
      }

      @if (selectedKind() === 'registered_remote') {
        <section class="editor" aria-labelledby="remote-title">
          <h2 id="remote-title">Registrierte Remotes</h2>
          <app-git-authorization-onboarding
            (provisioned)="onGitAuthorizationProvisioned($event)"
          />
          <app-public-git-remote-onboarding
            [projectId]="projectId"
            (remoteCreated)="onPublicRemoteCreated($event)"
          />
          <label for="remote-name">Anzeigename</label>
          <input
            id="remote-name"
            data-testid="remote-display-name"
            [ngModel]="displayName()"
            (ngModelChange)="displayName.set($event)"
          />
          <label for="remote-sensitivity">Sensitivitaet</label>
          <select
            id="remote-sensitivity"
            [ngModel]="sensitivity()"
            (ngModelChange)="sensitivity.set($event)"
          >
            <option value="public">Public</option>
            <option value="internal">Internal</option>
            <option value="confidential">Confidential</option>
            <option value="restricted">Restricted</option>
          </select>
          <label for="remote-option">Remote</label>
          <select
            id="remote-option"
            data-testid="remote-catalog"
            [ngModel]="selectedRemoteId()"
            (ngModelChange)="selectedRemoteId.set($event)"
          >
            <option value="">Auswaehlen</option>
            @for (remote of remotes(); track remote.remoteId) {
              <option [value]="remote.remoteId" [disabled]="!remote.active">
                {{ remote.label }} ({{ remote.kind }}, {{ remote.state }})
              </option>
            }
          </select>
          <p class="notice" role="status">
            Nur die serverregistrierte Remote-ID wird gesendet. URL, Credentials und
            Connection-Identity bleiben Hub-Verantwortung.
          </p>
        </section>
      }

      <section class="profile-catalog" aria-labelledby="profile-title">
        <h2 id="profile-title">Verfuegbare Indexprofile</h2>
        <p class="hint">
          Diese Auswahl dient nur der Orientierung. Ein Indexprofil wird erst beim
          serverseitigen Start eines Indexlaufs revisionsgebunden uebermittelt.
        </p>
        <label for="profile-option">Profil</label>
        <select
          id="profile-option"
          [ngModel]="selectedProfileId()"
          (ngModelChange)="selectedProfileId.set($event)"
        >
          <option value="">Noch kein Profil gewaehlt</option>
          @for (profile of indexProfiles(); track profile.profileId) {
            <option [value]="profile.profileId">
              {{ profile.label }}{{ profile.isDefault ? ' (Default)' : '' }}
            </option>
          }
        </select>
      </section>

      @if (catalogError()) {
        <p class="notice error" role="alert">{{ catalogError() }}</p>
      }
      @if (submitError()) {
        <p class="notice error" role="alert">{{ submitError() }}</p>
      }
      @if (completed()) {
        <p class="notice success" role="status" data-testid="content-admission-success">
          Quelle wurde vom Hub validiert und aufgenommen.
        </p>
      }

      <div class="actions">
        <button
          type="button"
          data-testid="submit-source"
          [disabled]="!canSubmit() || submitting()"
          (click)="submit()"
        >
          {{ submitting() ? 'Hub validiert...' : 'Validieren und aufnehmen' }}
        </button>
      </div>
    </main>
  `,
  styles: [
    `
      :host {
        display: block;
      }
      .import-shell {
        max-width: 70rem;
        margin: 0 auto;
        padding: 2rem;
      }
      .eyebrow {
        text-transform: uppercase;
        letter-spacing: 0.14em;
        font-weight: 700;
      }
      .back-link {
        display: inline-block;
        margin-bottom: 1.5rem;
      }
      .source-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
        gap: 0.75rem;
        margin: 2rem 0;
      }
      .source-card {
        min-height: 9rem;
        padding: 1rem;
        border: 2px solid var(--border-color, #516170);
        border-radius: 0.75rem;
        text-align: left;
        background: var(--surface-color, #fff);
        color: inherit;
      }
      .source-card.selected {
        border-color: var(--accent-color, #005fcc);
        box-shadow: 0 0 0 2px var(--accent-color, #005fcc);
      }
      .source-card span,
      .source-card small {
        display: block;
        margin-top: 0.5rem;
      }
      .editor,
      .profile-catalog {
        display: grid;
        gap: 0.65rem;
        padding: 1.25rem;
        margin: 1rem 0;
        border: 1px solid var(--border-color, #8794a0);
        border-radius: 0.75rem;
      }
      input,
      select,
      textarea {
        width: 100%;
        box-sizing: border-box;
        padding: 0.65rem;
        font: inherit;
      }
      textarea {
        resize: vertical;
      }
      .hint,
      .notice {
        padding: 0.75rem;
        background: #edf4fa;
        color: #17324a;
      }
      .error {
        background: #ffe9e7;
        color: #6f160f;
      }
      .success {
        background: #e5f6ea;
        color: #124d24;
      }
      .actions {
        display: flex;
        justify-content: flex-end;
        margin-top: 1.5rem;
      }
      .actions button {
        min-height: 2.75rem;
        padding: 0.7rem 1.1rem;
      }
      @media (max-width: 42rem) {
        .import-shell {
          padding: 1rem;
        }
      }
    `,
  ],
})
export class SourceImportPageComponent implements OnInit {
  readonly catalog = inject(SourceConnectorCatalogService);
  private readonly api = inject(SourceControlV1GovernanceApiClient);
  private readonly connectionApi = inject(SourceControlV1ApiClient);
  private readonly route = inject(ActivatedRoute);

  readonly projectId = sourceProjectIdFromRoute(this.route.snapshot);
  readonly selectedKind = signal<ImportKind>('direct_text');
  readonly displayName = signal('');
  readonly sensitivity = signal<Sensitivity>('internal');
  readonly mediaType = signal<'text/plain' | 'text/markdown'>('text/plain');
  readonly directContent = signal('');
  readonly notebookJson = signal(
    JSON.stringify({ cells: [{ cell_type: 'markdown', source: '', outputs: [] }] }, null, 2),
  );
  readonly selectedWorkspaceId = signal('');
  readonly workspaceRelativePath = signal('');
  readonly selectedRemoteId = signal('');
  readonly selectedProfileId = signal('');
  readonly workspaces = signal<readonly SourceWorkspaceOption[]>([]);
  readonly remotes = signal<readonly SourceRemoteOption[]>([]);
  readonly indexProfiles = signal<readonly SourceIndexProfileOption[]>([]);
  readonly loadingCatalogs = signal(false);
  readonly submitting = signal(false);
  readonly catalogError = signal('');
  readonly submitError = signal('');
  readonly completed = signal(false);
  readonly admissionPreview = signal<unknown | null>(null);

  readonly canSubmit = computed(() => {
    if (!this.projectId || this.submitting()) {
      return false;
    }
    const capability = this.catalog.capabilities.find(
      (candidate) => candidate.kind === this.selectedKind(),
    );
    if (!capability?.available) {
      return false;
    }
    if (this.selectedKind() === 'direct_text') {
      return Boolean(this.displayName().trim() && this.directContent().trim());
    }
    if (this.selectedKind() === 'open_notebook') {
      return Boolean(this.displayName().trim() && canonicalNotebookFromText(this.notebookJson()));
    }
    if (this.selectedKind() === 'registered_workspace') {
      const relativePath = normalizeSourceWorkspaceRelativePath(
        this.workspaceRelativePath(),
      );
      return Boolean(
        relativePath !== null &&
        this.displayName().trim() &&
          this.workspaces().some(
            (workspace) =>
              workspace.workspaceId === this.selectedWorkspaceId() && workspace.enabled,
          ),
      );
    }
    if (this.selectedKind() === 'registered_remote') {
      return Boolean(
        this.displayName().trim() &&
          this.remotes().some(
            (remote) => remote.remoteId === this.selectedRemoteId() && remote.active,
          ),
      );
    }
    return false;
  });

  ngOnInit(): void {
    if (!this.projectId) {
      return;
    }
    this.loadingCatalogs.set(true);
    forkJoin({
      workspaces: this.catalog.loadWorkspaces(this.projectId),
      remotes: this.catalog.loadRemotes(this.projectId),
      profiles: this.catalog.loadIndexProfiles(this.projectId),
    })
      .pipe(finalize(() => this.loadingCatalogs.set(false)))
      .subscribe({
        next: ({ workspaces, remotes, profiles }) => {
          this.workspaces.set(workspaces);
          this.remotes.set(remotes);
          this.indexProfiles.set(profiles);
          const defaultProfile = profiles.find((profile) => profile.isDefault);
          this.selectedProfileId.set(defaultProfile?.profileId ?? '');
        },
        error: () => {
          this.catalogError.set('Die serverseitigen Source-Control-Kataloge sind nicht verfuegbar.');
        },
      });
  }

  selectKind(capability: SourceConnectorCapability): void {
    if (!capability.available) {
      return;
    }
    this.selectedKind.set(capability.kind);
    this.submitError.set('');
    this.completed.set(false);
    this.admissionPreview.set(null);
  }

  onGitAuthorizationProvisioned(
    authorization: SourceControlGitAuthorizationView,
  ): void {
    this.reloadRegisteredRemotes(null, authorization.repository);
  }

  onWorkspaceRegistered(workspace: SourceControlWorkspaceRegistration): void {
    if (!this.projectId || workspace.state !== 'active') {
      return;
    }
    this.loadingCatalogs.set(true);
    this.catalogError.set('');
    this.catalog
      .loadWorkspaces(this.projectId)
      .pipe(finalize(() => this.loadingCatalogs.set(false)))
      .subscribe({
        next: (workspaces) => {
          this.workspaces.set(workspaces);
          const matchingWorkspace = workspaces.find(
            (candidate) =>
              candidate.enabled && candidate.workspaceId === workspace.workspace_id,
          );
          this.selectedWorkspaceId.set(matchingWorkspace?.workspaceId ?? '');
        },
        error: () => {
          this.catalogError.set(
            'Der Workspace-Katalog konnte nach der Registrierung nicht geladen werden.',
          );
        },
      });
  }

  onPublicRemoteCreated(remote: SourceControlPublicRemoteCreation): void {
    this.reloadRegisteredRemotes(remote.remote_id, null);
  }

  private reloadRegisteredRemotes(
    preferredRemoteId: string | null,
    preferredRepository: string | null,
  ): void {
    if (!this.projectId) {
      return;
    }
    this.loadingCatalogs.set(true);
    this.catalogError.set('');
    this.catalog
      .loadRemotes(this.projectId)
      .pipe(finalize(() => this.loadingCatalogs.set(false)))
      .subscribe({
        next: (remotes) => {
          this.remotes.set(remotes);
          const matchingRemote = remotes.find(
            (remote) =>
              remote.active
              && (
                (preferredRemoteId !== null && remote.remoteId === preferredRemoteId)
                || (
                  preferredRemoteId === null
                  && preferredRepository !== null
                  && remote.repository === preferredRepository
                )
              ),
          );
          this.selectedRemoteId.set(matchingRemote?.remoteId ?? '');
        },
        error: () => {
          this.catalogError.set(
            'Der Remote-Katalog konnte nach der Autorisierung nicht geladen werden.',
          );
        },
      });
  }

  submit(): void {
    if (!this.canSubmit()) {
      this.submitError.set('Die Eingaben sind unvollstaendig oder nicht serverregistriert.');
      return;
    }

    if (
      this.selectedKind() === 'registered_workspace' ||
      this.selectedKind() === 'registered_remote'
    ) {
      this.submitConnection();
      return;
    }

    const request = this.contentRequest();
    if (!request) {
      this.submitError.set('Das Notebook entspricht nicht dem kanonischen Hub-Vertrag.');
      return;
    }

    this.submitting.set(true);
    this.completed.set(false);
    this.submitError.set('');
    this.api
      .validateContentAdmission(request)
      .pipe(
        switchMap((validation) => {
          if (!validation.valid) {
            return throwError(() => new Error('Content admission was rejected'));
          }
          this.admissionPreview.set(validation.preview);
          return this.api.createContentAdmission(
            request,
            `ui:content:create:${crypto.randomUUID()}`,
          );
        }),
        finalize(() => this.submitting.set(false)),
      )
      .subscribe({
        next: () => this.completed.set(true),
        error: () => {
          this.submitError.set(
            'Die Quelle konnte vom Hub nicht validiert und aufgenommen werden.',
          );
        },
      });
  }

  private submitConnection(): void {
    const intent = this.connectionIntent();
    if (!intent) {
      this.submitError.set('Die ausgewaehlte Hub-Registrierung ist nicht mehr verfuegbar.');
      return;
    }
    this.submitting.set(true);
    this.completed.set(false);
    this.submitError.set('');
    this.connectionApi
      .validateConnection(intent)
      .pipe(
        switchMap(() =>
          this.connectionApi.createConnection(
            intent,
            `ui:connection:create:${crypto.randomUUID()}`,
          ),
        ),
        finalize(() => this.submitting.set(false)),
      )
      .subscribe({
        next: () => this.completed.set(true),
        error: () => {
          this.submitError.set(
            'Die registrierte Quelle konnte vom Hub nicht validiert und angebunden werden.',
          );
        },
      });
  }

  private connectionIntent() {
    if (this.selectedKind() === 'registered_workspace') {
      const workspace = this.workspaces().find(
        (item) => item.workspaceId === this.selectedWorkspaceId() && item.enabled,
      );
      const relativePath = normalizeSourceWorkspaceRelativePath(
        this.workspaceRelativePath(),
      );
      return workspace && relativePath !== null
        ? {
            connector_type: 'registered_workspace' as const,
            workspace_id: workspace.workspaceId,
            ...(relativePath ? { relative_path: relativePath } : {}),
            display_name: this.displayName().trim(),
            sensitivity: this.sensitivity(),
          }
        : null;
    }
    if (this.selectedKind() === 'registered_remote') {
      const remote = this.remotes().find(
        (item) => item.remoteId === this.selectedRemoteId() && item.active,
      );
      return remote
        ? {
            connector_type: remote.kind,
            remote_id: remote.remoteId,
            display_name: this.displayName().trim(),
            sensitivity: this.sensitivity(),
          }
        : null;
    }
    return null;
  }

  private contentRequest() {
    if (this.selectedKind() === 'direct_text') {
      return {
        project_id: this.projectId,
        source_type: 'direct_text' as const,
        display_name: this.displayName().trim(),
        sensitivity: this.sensitivity(),
        content: this.directContent(),
        media_type: this.mediaType(),
      };
    }
    if (this.selectedKind() === 'open_notebook') {
      const notebook = canonicalNotebookFromText(this.notebookJson());
      return notebook
        ? {
            project_id: this.projectId,
            source_type: 'notebook' as const,
            display_name: this.displayName().trim(),
            sensitivity: this.sensitivity(),
            notebook,
          }
        : null;
    }
    return null;
  }
}

function canonicalNotebookFromText(value: string): CanonicalNotebook | null {
  try {
    return canonicalNotebook(JSON.parse(value));
  } catch {
    return null;
  }
}
