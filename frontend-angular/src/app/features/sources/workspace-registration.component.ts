import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  OnInit,
  Output,
  computed,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize, switchMap } from 'rxjs';

import type {
  SourceControlWorkspaceFolder,
  SourceControlWorkspaceFolderValidation,
  SourceControlWorkspaceRegistration,
} from '../../models/source-control-v1-governance.model';
import { SourceControlV1GovernanceApiClient } from '../../services/source-control-v1-governance-api.client';

@Component({
  selector: 'app-workspace-registration',
  standalone: true,
  imports: [CommonModule, FormsModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section
      class="workspace-registration"
      aria-labelledby="workspace-registration-title"
      [attr.aria-busy]="loading() || creating()"
    >
      <header>
        <div>
          <p class="eyebrow">Serverseitige Auswahl</p>
          <h3 id="workspace-registration-title">Workspace registrieren</h3>
        </div>
        <button type="button" (click)="reload()" [disabled]="loading() || creating()">
          Labels neu laden
        </button>
      </header>

      @if (loading()) {
        <p class="notice" role="status" aria-live="polite">
          Verfügbare Workspace-Labels werden geladen.
        </p>
      } @else if (folders().length === 0 && !errorMessage()) {
        <p class="notice" role="status">Keine auswählbaren Workspace-Labels vorhanden.</p>
      } @else if (folders().length > 0) {
        <label for="workspace-folder-label">Freigegebenes Label</label>
        <select
          id="workspace-folder-label"
          data-testid="workspace-folder-label"
          [ngModel]="selectedFolderHandle()"
          (ngModelChange)="selectFolder($event)"
          [disabled]="creating()"
        >
          <option value="">Auswählen</option>
          @for (folder of folders(); track folder.folder_handle) {
            <option [value]="folder.folder_handle">{{ folder.display_name }}</option>
          }
        </select>

        @if (selectedFolder(); as folder) {
          @if (folder.capabilities.read_only) {
            <p class="notice read-only" role="status">
              Dieses Workspace-Label wird ausschließlich read-only registriert.
            </p>
          }
          <p class="hint">
            Die Auswahl ist serverseitig begrenzt. Pfade und Dateinamen werden nicht offengelegt.
          </p>
        }

        <button
          type="button"
          data-testid="create-workspace-registration"
          (click)="submit()"
          [disabled]="!canSubmit()"
        >
          {{ creating() ? 'Hub validiert...' : 'Validieren und registrieren' }}
        </button>
      }

      @if (validation(); as preview) {
        <p class="notice" role="status">
          Auswahl validiert bis Epoch {{ preview.expires_at_epoch }}.
        </p>
      }
      @if (errorMessage()) {
        <p class="notice error" role="alert">{{ errorMessage() }}</p>
      }
      @if (successMessage()) {
        <p class="notice success" role="status">{{ successMessage() }}</p>
      }
    </section>
  `,
  styles: [`
    :host { display: block; }
    .workspace-registration { display: grid; gap: .75rem; padding: 1rem; border: 1px solid var(--border-color, #8794a0); border-radius: .65rem; background: color-mix(in srgb, var(--surface-color, #fff) 93%, #19745b); }
    header { display: flex; align-items: center; justify-content: space-between; gap: .7rem; flex-wrap: wrap; }
    h3, .eyebrow, .notice, .hint { margin: 0; }
    .eyebrow { font-size: .72rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
    select, button { min-height: 2.35rem; padding: .45rem .65rem; font: inherit; }
    select { width: 100%; }
    .hint { color: var(--muted, #516170); }
    .notice { padding: .55rem; background: #edf4fa; color: #17324a; }
    .read-only { background: #fff4d8; color: #6f4800; }
    .error { background: #ffe9e7; color: #8f1711; }
    .success { background: #e5f6ea; color: #087c56; }
  `],
})
export class WorkspaceRegistrationComponent implements OnInit {
  private readonly api = inject(SourceControlV1GovernanceApiClient);

  @Input() projectId = '';
  @Output() readonly workspaceCreated = new EventEmitter<SourceControlWorkspaceRegistration>();

  readonly folders = signal<readonly SourceControlWorkspaceFolder[]>([]);
  readonly selectedFolderHandle = signal('');
  readonly loading = signal(false);
  readonly creating = signal(false);
  readonly validation = signal<SourceControlWorkspaceFolderValidation | null>(null);
  readonly errorMessage = signal('');
  readonly successMessage = signal('');

  readonly selectedFolder = computed(() =>
    this.folders().find(
      (folder) => folder.folder_handle === this.selectedFolderHandle(),
    ) ?? null,
  );

  readonly canSubmit = computed(() =>
    Boolean(this.projectId.trim())
      && !this.loading()
      && !this.creating()
      && this.selectedFolder() !== null,
  );

  ngOnInit(): void {
    this.reload();
  }

  reload(): void {
    if (this.creating()) {
      return;
    }
    const projectId = this.projectId.trim();
    if (!projectId) {
      this.folders.set([]);
      this.selectedFolderHandle.set('');
      this.errorMessage.set('Ohne Projektkontext bleiben Workspace-Labels gesperrt.');
      return;
    }
    this.loading.set(true);
    this.errorMessage.set('');
    this.successMessage.set('');
    this.api
      .listWorkspaceFolders(projectId)
      .pipe(finalize(() => this.loading.set(false)))
      .subscribe({
        next: (page) => {
          this.folders.set(page.items);
          if (!page.items.some(
            (folder) => folder.folder_handle === this.selectedFolderHandle(),
          )) {
            this.selectedFolderHandle.set('');
          }
        },
        error: () => {
          this.folders.set([]);
          this.selectedFolderHandle.set('');
          this.errorMessage.set('Workspace-Labels konnten nicht sicher geladen werden.');
        },
      });
  }

  selectFolder(folderHandle: string): void {
    this.selectedFolderHandle.set(
      this.folders().some((folder) => folder.folder_handle === folderHandle)
        ? folderHandle
        : '',
    );
    this.validation.set(null);
    this.errorMessage.set('');
    this.successMessage.set('');
  }

  submit(): void {
    const projectId = this.projectId.trim();
    const folder = this.selectedFolder();
    if (!projectId || !folder || this.creating()) {
      this.errorMessage.set('Es ist kein freigegebenes Workspace-Label ausgewählt.');
      return;
    }

    this.creating.set(true);
    this.validation.set(null);
    this.errorMessage.set('');
    this.successMessage.set('');
    this.api
      .validateWorkspaceFolder(projectId, folder.folder_handle)
      .pipe(
        switchMap((validation) => {
          this.validation.set(validation);
          return this.api.createWorkspaceRegistration(
            projectId,
            validation.validation_handle,
            `ui:workspace:create:${crypto.randomUUID()}`,
          );
        }),
        finalize(() => this.creating.set(false)),
      )
      .subscribe({
        next: (workspace) => {
          this.successMessage.set('Der Workspace wurde serverseitig registriert.');
          this.workspaceCreated.emit(workspace);
        },
        error: () => {
          this.errorMessage.set(
            'Der Hub konnte das Workspace-Label nicht validieren und registrieren.',
          );
        },
      });
  }
}
