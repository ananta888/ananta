import { Component, inject } from '@angular/core';

import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { UiSkeletonComponent } from './ui-skeleton.component';
import { AdminFacade } from '../features/admin/admin.facade';

@Component({
  standalone: true,
  selector: 'app-knowledge',
  imports: [FormsModule, UiSkeletonComponent],
  styles: [`
    .card { border: 1px solid var(--border); border-radius: 12px; padding: 18px; background: var(--card-bg); }
    .card + .card { margin-top: 16px; }
    .card-light { background: color-mix(in srgb, var(--card-bg) 85%, white); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
    .row { display: flex; align-items: center; gap: 12px; }
    .space-between { justify-content: space-between; }
    .mt-sm { margin-top: 10px; }
    .mt-md { margin-top: 18px; }
    .mt-5 { margin-top: 5px; }
    .no-margin { margin: 0; }
    .muted { color: var(--muted, #666); }
    .font-sm { font-size: 12px; }
    .flex-1 { flex: 1; min-width: 0; }
    .title-muted { font-size: 13px; margin: 4px 0 0; }

    .upload-row  { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; }
    .search-row  { display: flex; flex-wrap: wrap; gap: 10px; align-items: end; }
    .label-no-margin { display: flex; flex-direction: column; gap: 4px; font-size: 13px; font-weight: 500; }
    .label-no-margin input, .label-no-margin select { margin-top: 2px; }

    .pill { display: inline-flex; align-items: center; border-radius: 999px; padding: 3px 10px; background: rgba(0,0,0,0.06); font-size: 12px; }
    .pills { display: flex; flex-wrap: wrap; gap: 8px; }
    .meta  { display: flex; flex-wrap: wrap; gap: 8px; font-size: 12px; color: var(--muted, #666); margin-top: 6px; }

    .item-list  { display: grid; gap: 10px; max-height: 70vh; overflow: auto; }
    .item-btn   { border: 1px solid var(--border); border-radius: 10px; padding: 12px; background: var(--card-bg); cursor: pointer; text-align: left; }
    .item-btn.active { border-color: var(--primary-color, #007bff); box-shadow: 0 0 0 1px color-mix(in srgb, var(--primary-color,#007bff) 25%,transparent); }
    .empty { padding: 20px; text-align: center; color: var(--muted, #666); }

    .profile-card { border: 1px dashed var(--border); border-radius: 10px; padding: 10px; background: color-mix(in srgb, var(--card-bg) 88%, white); }
    .results { display: grid; gap: 10px; }
    .pre { max-height: 280px; overflow: auto; white-space: pre-wrap; word-break: break-word; }
  `],
  template: `
    <div class="card">
      <div class="row space-between">
        <div>
          <h2 class="no-margin">Wissen</h2>
          <p class="muted title-muted">Dateien hochladen und in Knowledge Collections für RAG-Suche organisieren.</p>
        </div>
        <button class="secondary" (click)="refresh()" [disabled]="loadingCollections || uploadBusy">Aktualisieren</button>
      </div>
    </div>

    <!-- Upload -->
    <div class="card mt-md">
      <h3 class="no-margin">Datei hochladen</h3>
      <div class="upload-row mt-sm">
        <div class="flex-1">
          <label class="label-no-margin">Knowledge Collection (optional)
            <input [(ngModel)]="collectionName" placeholder="z.B. product-docs oder sprint-review" />
          </label>
        </div>
        <div class="flex-1">
          <label class="label-no-margin">Datei
            <input type="file" (change)="onFileSelected($event)" />
          </label>
        </div>
        <button (click)="upload()" [disabled]="uploadBusy || !selectedFile">
          {{ uploadBusy ? 'Lade hoch…' : 'Upload starten' }}
        </button>
      </div>
      @if (selectedFile) {
        <div class="meta">
          <span class="pill">{{ selectedFile.name }}</span>
          <span class="pill">{{ selectedFile.size }} bytes</span>
          <span class="pill">{{ selectedFile.type || 'unknown' }}</span>
        </div>
      }
    </div>

    <!-- Collections -->
    <div class="card mt-md">
      <div class="row space-between">
        <div>
          <h3 class="no-margin">Knowledge Collections</h3>
          <p class="muted title-muted">Sammlungen gruppieren Wissen für gezielte Suche.</p>
        </div>
        <button class="secondary" (click)="loadCollections()" [disabled]="loadingCollections || collectionBusy">Neu laden</button>
      </div>

      <div class="upload-row mt-sm">
        <div class="flex-1">
          <label class="label-no-margin">Neue Collection
            <input [(ngModel)]="newCollectionName" placeholder="z.B. payments-docs" />
          </label>
        </div>
        <div class="flex-1">
          <label class="label-no-margin">Beschreibung (optional)
            <input [(ngModel)]="newCollectionDescription" placeholder="Kurzbeschreibung" />
          </label>
        </div>
        <button (click)="createCollection()" [disabled]="collectionBusy || !newCollectionName.trim()">
          {{ collectionBusy ? 'Speichere…' : 'Collection anlegen' }}
        </button>
      </div>

      <div class="upload-row mt-sm">
        <div class="flex-1">
          <label class="label-no-margin">Collection-Profil
            <select [(ngModel)]="selectedCollectionProfileName">
              @for (profile of knowledgeProfiles; track profile.name) {
                <option [value]="profile.name">{{ profile.label }}</option>
              }
            </select>
          </label>
        </div>
        <div class="flex-1">
          <div class="profile-card">
            <strong>{{ activeCollectionProfile()?.label || 'Kein Profil' }}</strong>
            <div class="muted font-sm mt-5">{{ activeCollectionProfile()?.description || 'Kein Profil geladen.' }}</div>
          </div>
        </div>
      </div>

      @if (loadingCollections) {
        <app-ui-skeleton [count]="1" [lineCount]="4"></app-ui-skeleton>
      } @else if (!knowledgeCollections.length) {
        <div class="empty">Noch keine Knowledge Collections vorhanden.</div>
      } @else {
        <div class="item-list mt-sm">
          @for (collection of knowledgeCollections; track collection.id) {
            <button class="item-btn" [class.active]="collection.id === selectedCollectionId" (click)="selectCollection(collection.id)">
              <div class="row space-between">
                <strong>{{ collection.name }}</strong>
                <span class="badge">{{ collection.created_by || 'system' }}</span>
              </div>
              <div class="meta">
                <span>{{ collection.description || 'Keine Beschreibung' }}</span>
              </div>
            </button>
          }
        </div>
      }
    </div>

    <!-- Collection Search -->
    <div class="card mt-md">
      <div class="row space-between">
        <div>
          <h3 class="no-margin">Collection Search</h3>
          <p class="muted title-muted">Gezielte Suche über indexierte Artefakte einer Collection.</p>
        </div>
        @if (selectedCollectionDetail?.collection?.name) {
          <span class="pill">{{ selectedCollectionDetail.collection.name }}</span>
        }
      </div>

      <div class="search-row mt-sm">
        <div class="flex-1">
          <label class="label-no-margin">Suchanfrage
            <input [(ngModel)]="knowledgeSearchQuery" placeholder="z.B. timeout, payment flow, adapter" />
          </label>
        </div>
        <label class="label-no-margin">
          <span class="muted font-sm">Profil</span>
          <select [(ngModel)]="selectedCollectionProfileName">
            @for (profile of knowledgeProfiles; track profile.name) {
              <option [value]="profile.name">{{ profile.label }}</option>
            }
          </select>
        </label>
        <button class="secondary" (click)="indexSelectedCollection()" [disabled]="collectionIndexBusy || !selectedCollectionId">
          {{ collectionIndexBusy ? 'Indexiere…' : 'Collection indexieren' }}
        </button>
        <button (click)="searchSelectedCollection()" [disabled]="searchBusy || !selectedCollectionId || !knowledgeSearchQuery.trim()">
          {{ searchBusy ? 'Suche…' : 'Collection durchsuchen' }}
        </button>
      </div>

      @if (selectedCollectionDetail) {
        <div class="pills mt-sm">
          <span class="pill">{{ selectedCollectionDetail.knowledge_links?.length || 0 }} Links</span>
          <span class="pill">{{ selectedCollectionDetail.knowledge_indices?.length || 0 }} Indizes</span>
        </div>
      }

      @if (knowledgeSearchResults.length) {
        <div class="results mt-sm">
          @for (chunk of knowledgeSearchResults; track $index) {
            <div class="card-light">
              <div class="row space-between">
                <strong>{{ chunk.source }}</strong>
                <span class="badge">{{ chunk.metadata?.record_kind || chunk.engine }}</span>
              </div>
              <div class="muted font-sm mt-5">{{ chunk.metadata?.artifact_id || 'unknown artifact' }}</div>
              <pre class="pre">{{ chunk.content }}</pre>
            </div>
          }
        </div>
      } @else {
        <div class="muted mt-sm">Noch keine Suchergebnisse.</div>
      }
    </div>
  `,
})
export class KnowledgeComponent {
  private dir    = inject(AgentDirectoryService);
  private hubApi = inject(AdminFacade);
  private ns     = inject(NotificationService);

  hub = this.dir.list().find((a) => a.role === 'hub');

  // ── upload ───────────────────────────────────────────────────────────────────
  selectedFile: File | null = null;
  collectionName = '';
  uploadBusy = false;

  // ── collections ──────────────────────────────────────────────────────────────
  knowledgeCollections: any[] = [];
  loadingCollections = false;
  collectionBusy = false;
  collectionIndexBusy = false;
  selectedCollectionId: string | null = null;
  selectedCollectionDetail: any = null;
  newCollectionName = '';
  newCollectionDescription = '';

  // ── profiles + search ────────────────────────────────────────────────────────
  knowledgeProfiles: any[] = [];
  selectedCollectionProfileName = 'default';
  searchBusy = false;
  knowledgeSearchQuery = '';
  knowledgeSearchResults: any[] = [];

  constructor() {
    this.loadCollections();
    this.loadProfiles();
  }

  refresh(): void {
    this.loadCollections();
    this.loadProfiles();
  }

  // ── upload ───────────────────────────────────────────────────────────────────
  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement | null;
    this.selectedFile = input?.files?.[0] || null;
  }

  upload(): void {
    if (!this.hub || !this.selectedFile) return;
    this.uploadBusy = true;
    this.hubApi.uploadArtifact(this.hub.url, this.selectedFile, this.collectionName).pipe(
      finalize(() => { this.uploadBusy = false; }),
    ).subscribe({
      next: () => {
        this.ns.success('Artefakt hochgeladen — in Ergebnisse sichtbar');
        this.selectedFile = null;
        this.collectionName = '';
        this.loadCollections();
      },
      error: (e) => this.ns.error(this.ns.fromApiError(e, 'Upload fehlgeschlagen')),
    });
  }

  // ── profiles ─────────────────────────────────────────────────────────────────
  private loadProfiles(): void {
    if (!this.hub) return;
    this.hubApi.listKnowledgeIndexProfiles(this.hub.url).subscribe({
      next: (payload) => {
        const items = Array.isArray(payload?.items) ? payload.items : [];
        this.knowledgeProfiles = items;
        const def = items.find((i: any) => i?.is_default)?.name || items[0]?.name || 'default';
        if (!this.selectedCollectionProfileName || this.selectedCollectionProfileName === 'default') {
          this.selectedCollectionProfileName = def;
        }
      },
      error: () => { this.knowledgeProfiles = []; },
    });
  }

  // ── collections ──────────────────────────────────────────────────────────────
  loadCollections(): void {
    if (!this.hub) return;
    this.loadingCollections = true;
    this.hubApi.listKnowledgeCollections(this.hub.url).pipe(
      finalize(() => { this.loadingCollections = false; }),
    ).subscribe({
      next: (items) => {
        this.knowledgeCollections = Array.isArray(items) ? items : [];
        if (!this.selectedCollectionId && this.knowledgeCollections.length) {
          this.selectCollection(this.knowledgeCollections[0].id);
        }
      },
      error: (e) => this.ns.error(this.ns.fromApiError(e, 'Collections konnten nicht geladen werden')),
    });
  }

  createCollection(): void {
    if (!this.hub || !this.newCollectionName.trim()) return;
    this.collectionBusy = true;
    this.hubApi.createKnowledgeCollection(this.hub.url, {
      name: this.newCollectionName.trim(),
      description: this.newCollectionDescription.trim() || undefined,
    }).pipe(finalize(() => { this.collectionBusy = false; })).subscribe({
      next: (col) => {
        this.ns.success('Collection angelegt');
        this.newCollectionName = '';
        this.newCollectionDescription = '';
        this.loadCollections();
        if (col?.id) this.selectCollection(col.id);
      },
      error: (e) => this.ns.error(this.ns.fromApiError(e, 'Collection konnte nicht angelegt werden')),
    });
  }

  selectCollection(collectionId: string): void {
    if (!this.hub || !collectionId) return;
    this.selectedCollectionId = collectionId;
    this.hubApi.getKnowledgeCollection(this.hub.url, collectionId).subscribe({
      next: (p) => { this.selectedCollectionDetail = p; },
      error: (e) => this.ns.error(this.ns.fromApiError(e, 'Collection-Details konnten nicht geladen werden')),
    });
  }

  indexSelectedCollection(): void {
    if (!this.hub || !this.selectedCollectionId) return;
    this.collectionIndexBusy = true;
    this.hubApi.indexKnowledgeCollection(this.hub.url, this.selectedCollectionId, {
      profile_name: this.selectedCollectionProfileName || 'default',
    }).pipe(finalize(() => { this.collectionIndexBusy = false; })).subscribe({
      next: () => { this.ns.success('Collection indexiert'); this.selectCollection(this.selectedCollectionId!); },
      error: (e) => this.ns.error(this.ns.fromApiError(e, 'Collection-Index fehlgeschlagen')),
    });
  }

  searchSelectedCollection(): void {
    if (!this.hub || !this.selectedCollectionId || !this.knowledgeSearchQuery.trim()) return;
    this.searchBusy = true;
    this.hubApi.searchKnowledgeCollection(this.hub.url, this.selectedCollectionId, {
      query: this.knowledgeSearchQuery.trim(),
      top_k: 5,
    }).pipe(finalize(() => { this.searchBusy = false; })).subscribe({
      next: (p) => { this.knowledgeSearchResults = Array.isArray(p?.chunks) ? p.chunks : []; },
      error: (e) => this.ns.error(this.ns.fromApiError(e, 'Collection-Suche fehlgeschlagen')),
    });
  }

  activeCollectionProfile(): any {
    return this.knowledgeProfiles.find((p: any) => p?.name === this.selectedCollectionProfileName) || null;
  }
}
