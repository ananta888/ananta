import { CommonModule } from '@angular/common';
import {
  HttpErrorResponse,
  HttpEventType,
  HttpResponse,
} from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Output,
  computed,
  effect,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { ProjectContextService } from '../../services/project-context.service';
import {
  WorkspaceSnapshotApiClient,
  WorkspaceSnapshotFile,
  WorkspaceSnapshotUploadResponse,
} from '../../services/workspace-snapshot-api.client';

export const WORKSPACE_SNAPSHOT_LIMITS = Object.freeze({
  maxFiles: 2_000,
  maxFileBytes: 20 * 1024 * 1024,
  maxTotalBytes: 200 * 1024 * 1024,
  maxRelativePathBytes: 512,
  maxDisplayNameCharacters: 80,
});

const CONTROL_DIRECTORIES = new Set([
  '.git',
  '.hg',
  '.svn',
  '.ananta',
]);

export interface WorkspaceSnapshotSelection {
  readonly files: readonly WorkspaceSnapshotFile[];
  readonly totalBytes: number;
  readonly rootName: string;
  readonly error: string;
}

export function validateWorkspaceSnapshotSelection(
  selectedFiles: readonly File[],
): WorkspaceSnapshotSelection {
  if (selectedFiles.length === 0) {
    return invalidSelection('Der ausgewählte Ordner enthält keine Dateien.');
  }
  if (selectedFiles.length > WORKSPACE_SNAPSHOT_LIMITS.maxFiles) {
    return invalidSelection(`Der Ordner überschreitet das Limit von ${WORKSPACE_SNAPSHOT_LIMITS.maxFiles} Dateien.`);
  }

  const uploads: WorkspaceSnapshotFile[] = [];
  const canonicalPaths = new Set<string>();
  let totalBytes = 0;
  let rootName = '';
  for (const file of selectedFiles) {
    const relativePath = String(file.webkitRelativePath || '');
    const segments = relativePath.split('/');
    if (
      !relativePath
      || relativePath.startsWith('/')
      || relativePath.includes('\\')
      || segments.length < 2
      || segments.some((segment) => !segment || segment === '.' || segment === '..')
    ) {
      return invalidSelection('Mindestens eine Datei besitzt keinen sicheren Ordner-Relativpfad.');
    }
    if (new TextEncoder().encode(relativePath).byteLength > WORKSPACE_SNAPSHOT_LIMITS.maxRelativePathBytes) {
      return invalidSelection(`Ein Relativpfad überschreitet ${WORKSPACE_SNAPSHOT_LIMITS.maxRelativePathBytes} UTF-8-Bytes.`);
    }
    if (segments.some((segment) => CONTROL_DIRECTORIES.has(segment.toLocaleLowerCase('en-US')))) {
      return invalidSelection('Versionskontroll- und Ananta-Steuerverzeichnisse dürfen nicht hochgeladen werden.');
    }
    if (file.size === 0) {
      return invalidSelection('Leere Dateien werden aus Sicherheitsgründen nicht als Snapshot übernommen.');
    }
    if (file.size > WORKSPACE_SNAPSHOT_LIMITS.maxFileBytes) {
      return invalidSelection(`Eine Datei überschreitet ${formatSnapshotBytes(WORKSPACE_SNAPSHOT_LIMITS.maxFileBytes)}.`);
    }

    totalBytes += file.size;
    if (totalBytes > WORKSPACE_SNAPSHOT_LIMITS.maxTotalBytes) {
      return invalidSelection(`Der Ordner überschreitet ${formatSnapshotBytes(WORKSPACE_SNAPSHOT_LIMITS.maxTotalBytes)} Gesamtgröße.`);
    }
    const canonicalPath = relativePath.normalize('NFC').toLocaleLowerCase('en-US');
    if (canonicalPaths.has(canonicalPath)) {
      return invalidSelection('Der Ordner enthält Dateipfade, die sich nur durch Groß-/Kleinschreibung unterscheiden.');
    }
    canonicalPaths.add(canonicalPath);
    rootName ||= segments[0];
    uploads.push({ file, relativePath });
  }

  return { files: uploads, totalBytes, rootName, error: '' };
}

function invalidSelection(error: string): WorkspaceSnapshotSelection {
  return { files: [], totalBytes: 0, rootName: '', error };
}

export function formatSnapshotBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

@Component({
  selector: 'app-workspace-snapshot-upload',
  standalone: true,
  imports: [CommonModule, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="snapshot-upload" aria-labelledby="snapshot-upload-title" [attr.aria-busy]="uploading()">
      <header>
        <div>
          <p class="eyebrow">Browser-Ordner</p>
          <h3 id="snapshot-upload-title">Workspace-Snapshot hochladen</h3>
        </div>
        <span class="project-state" [class.blocked]="!projectId()">
          {{ projectId() ? 'Projektkontext aktiv' : 'Projekt auswählen' }}
        </span>
      </header>

      <p class="hint">
        Der Browser überträgt ausschließlich die ausgewählten Dateien und ihre relativen Ordnerpfade.
        Lokale absolute Pfade und Zugangsdaten werden nicht gesendet.
      </p>
      <dl class="limits" aria-label="Upload-Limits">
        <div><dt>Dateien</dt><dd>max. {{ limits.maxFiles }}</dd></div>
        <div><dt>Pro Datei</dt><dd>{{ formatBytes(limits.maxFileBytes) }}</dd></div>
        <div><dt>Gesamt</dt><dd>{{ formatBytes(limits.maxTotalBytes) }}</dd></div>
        <div><dt>Relativpfad</dt><dd>{{ limits.maxRelativePathBytes }} UTF-8-Bytes</dd></div>
      </dl>

      <label for="workspace-snapshot-folder">Ordner auswählen</label>
      <input
        id="workspace-snapshot-folder"
        data-testid="workspace-snapshot-folder"
        type="file"
        webkitdirectory
        multiple
        (change)="onFolderSelected($event)"
        [disabled]="uploading() || !projectId()"
      />

      <label for="workspace-snapshot-name">Anzeigename</label>
      <input
        id="workspace-snapshot-name"
        data-testid="workspace-snapshot-name"
        type="text"
        maxlength="80"
        [ngModel]="displayName()"
        (ngModelChange)="setDisplayName($event)"
        [disabled]="uploading()"
      />

      @if (fileCount() > 0) {
        <p class="selection" role="status">
          {{ fileCount() }} Dateien, {{ formatBytes(totalBytes()) }} ausgewählt.
        </p>
      }
      @if (uploading()) {
        <div class="progress-block" role="status" aria-live="polite">
          <progress max="100" [value]="progress()"></progress>
          <span>{{ progress() }} % übertragen</span>
        </div>
      }

      <button
        type="button"
        data-testid="upload-workspace-snapshot"
        (click)="upload()"
        [disabled]="!canUpload()"
      >
        {{ uploading() ? 'Snapshot wird übertragen...' : 'Snapshot sicher registrieren' }}
      </button>

      @if (errorMessage()) {
        <p class="notice error" role="alert">{{ errorMessage() }}</p>
      }
      @if (successMessage()) {
        <p class="notice success" role="status">{{ successMessage() }}</p>
      }
    </section>
  `,
  styles: [`
    :host { display: block; margin-block: 1rem; }
    .snapshot-upload { display: grid; gap: .8rem; padding: 1rem; border: 1px solid var(--border-color, #81919a); border-radius: .7rem; background: linear-gradient(145deg, color-mix(in srgb, var(--surface-color, #fff) 94%, #0a7c64), var(--surface-color, #fff)); }
    header { display: flex; justify-content: space-between; align-items: start; gap: 1rem; flex-wrap: wrap; }
    h3, p, dl, dd { margin: 0; }
    .eyebrow { font-size: .72rem; font-weight: 800; letter-spacing: .12em; text-transform: uppercase; }
    .hint { color: var(--muted, #50606a); }
    .project-state { padding: .3rem .55rem; border-radius: 999px; background: #dff4e9; color: #086642; font-weight: 700; }
    .project-state.blocked { background: #ffe9e7; color: #8f1711; }
    .limits { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .5rem; }
    .limits div { padding: .55rem; border-radius: .45rem; background: color-mix(in srgb, var(--surface-color, #fff) 88%, #19745b); }
    dt { font-size: .74rem; color: var(--muted, #50606a); }
    dd { font-weight: 750; }
    input, button { min-height: 2.4rem; padding: .5rem .65rem; font: inherit; }
    input[type='text'] { width: 100%; box-sizing: border-box; }
    .selection, .notice { padding: .6rem; border-radius: .4rem; background: #edf4fa; color: #17324a; }
    .error { background: #ffe9e7; color: #8f1711; }
    .success { background: #e5f6ea; color: #087c56; }
    .progress-block { display: grid; grid-template-columns: 1fr auto; gap: .75rem; align-items: center; }
    progress { width: 100%; }
    @media (max-width: 720px) { .limits { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  `],
})
export class WorkspaceSnapshotUploadComponent {
  private readonly api = inject(WorkspaceSnapshotApiClient);
  private readonly projectContext = inject(ProjectContextService);

  @Output() readonly workspaceCreated = new EventEmitter<WorkspaceSnapshotUploadResponse>();

  readonly limits = WORKSPACE_SNAPSHOT_LIMITS;
  readonly projectId = computed(() => this.projectContext.selectedProjectId()?.trim() ?? '');
  readonly selectedFiles = signal<readonly WorkspaceSnapshotFile[]>([]);
  readonly totalBytes = signal(0);
  readonly displayName = signal('');
  readonly uploading = signal(false);
  readonly progress = signal(0);
  readonly errorMessage = signal('');
  readonly successMessage = signal('');
  private readonly idempotencyKey = signal('');
  private readonly uploadProjectId = signal('');
  private observedProjectId = this.projectId();

  readonly fileCount = computed(() => this.selectedFiles().length);
  readonly canUpload = computed(() =>
    Boolean(this.projectId())
      && this.fileCount() > 0
      && this.displayName().trim().length > 0
      && this.displayName().trim().length <= WORKSPACE_SNAPSHOT_LIMITS.maxDisplayNameCharacters
      && !this.uploading(),
  );

  constructor() {
    effect(() => {
      const nextProjectId = this.projectId();
      if (nextProjectId === this.observedProjectId) return;
      this.observedProjectId = nextProjectId;
      this.selectedFiles.set([]);
      this.totalBytes.set(0);
      this.displayName.set('');
      this.progress.set(0);
      this.idempotencyKey.set('');
      if (!this.uploading()) {
        this.errorMessage.set(
          nextProjectId
            ? 'Die Ordnerauswahl wurde wegen des Projektwechsels zurückgesetzt.'
            : 'Ohne aktiven Projektkontext ist kein Upload möglich.',
        );
      }
      this.successMessage.set('');
    });
  }

  onFolderSelected(event: Event): void {
    const input = event.target as HTMLInputElement | null;
    const selection = validateWorkspaceSnapshotSelection(
      input?.files ? Array.from(input.files) : [],
    );
    this.errorMessage.set(selection.error);
    this.successMessage.set('');
    this.selectedFiles.set(selection.files);
    this.totalBytes.set(selection.totalBytes);
    this.progress.set(0);
    if (selection.files.length > 0 && !this.displayName().trim()) {
      this.displayName.set(selection.rootName.slice(0, WORKSPACE_SNAPSHOT_LIMITS.maxDisplayNameCharacters));
    }
    this.refreshIdempotencyKey();
  }

  setDisplayName(value: string): void {
    this.displayName.set(String(value ?? ''));
    this.errorMessage.set('');
    this.successMessage.set('');
    this.refreshIdempotencyKey();
  }

  upload(): void {
    if (!this.canUpload()) {
      this.errorMessage.set(
        this.projectId()
          ? 'Bitte einen gültigen Ordner und Anzeigenamen auswählen.'
          : 'Ohne aktiven Projektkontext ist kein Upload möglich.',
      );
      return;
    }

    this.uploading.set(true);
    this.uploadProjectId.set(this.projectId());
    this.progress.set(0);
    this.errorMessage.set('');
    this.successMessage.set('');
    this.api.upload({
      projectId: this.projectId(),
      displayName: this.displayName().trim(),
      files: this.selectedFiles(),
      idempotencyKey: this.idempotencyKey(),
    }).pipe(finalize(() => {
      this.uploading.set(false);
      this.uploadProjectId.set('');
    })).subscribe({
      next: (event) => this.handleUploadEvent(event),
      error: (error: unknown) => {
        this.progress.set(0);
        this.errorMessage.set(this.safeErrorMessage(error));
      },
    });
  }

  formatBytes(bytes: number): string {
    return formatSnapshotBytes(bytes);
  }

  private handleUploadEvent(event: unknown): void {
    if (isUploadProgressEvent(event)) {
      const total = event.total || this.totalBytes();
      this.progress.set(total > 0 ? Math.min(99, Math.floor((event.loaded / total) * 100)) : 0);
      return;
    }
    if (!(event instanceof HttpResponse)) return;
    if (!this.uploadProjectId() || this.uploadProjectId() !== this.projectId()) {
      this.errorMessage.set('Das Projekt wurde während des Uploads gewechselt; die Antwort wurde verworfen.');
      return;
    }
    const workspace = event.body as WorkspaceSnapshotUploadResponse | null;
    if (!workspace || typeof workspace.workspace_id !== 'string' || workspace.state !== 'active') {
      this.errorMessage.set('Der Hub hat keine gültige Workspace-Registrierung zurückgegeben.');
      return;
    }
    this.progress.set(100);
    this.successMessage.set(
      workspace.replayed
        ? 'Der bereits sicher veröffentlichte Snapshot wurde wiederverwendet.'
        : `${workspace.file_count} Dateien wurden als Workspace registriert.`,
    );
    this.workspaceCreated.emit(workspace);
  }

  private refreshIdempotencyKey(): void {
    this.idempotencyKey.set(`workspace-snapshot:${crypto.randomUUID()}`);
  }

  private safeErrorMessage(error: unknown): string {
    const status = error instanceof HttpErrorResponse ? error.status : 0;
    if (status === 401 || status === 403) return 'Für dieses Projekt ist der Snapshot-Upload nicht freigegeben.';
    if (status === 409) return 'Dieser Upload-Key gehört bereits zu einem anderen Snapshot.';
    if (status === 413) return 'Der ausgewählte Ordner überschreitet die serverseitigen Upload-Limits.';
    if (status === 422) return 'Der Hub hat einen unsicheren oder ungültigen Ordnerinhalt abgelehnt.';
    return 'Der Workspace-Snapshot konnte nicht sicher übertragen werden.';
  }
}

function isUploadProgressEvent(
  event: unknown,
): event is { readonly type: HttpEventType.UploadProgress; readonly loaded: number; readonly total?: number } {
  return Boolean(
    event
      && typeof event === 'object'
      && (event as { type?: number }).type === HttpEventType.UploadProgress
      && typeof (event as { loaded?: unknown }).loaded === 'number',
  );
}
