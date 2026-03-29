import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';

@Component({
  standalone: true,
  selector: 'app-artifacts',
  imports: [CommonModule, FormsModule],
  styles: [`
    .artifact-layout { display: grid; grid-template-columns: minmax(320px, 420px) 1fr; gap: 16px; align-items: start; }
    .artifact-list { display: grid; gap: 10px; max-height: 70vh; overflow: auto; }
    .artifact-item { border: 1px solid var(--border); border-radius: 10px; padding: 12px; background: var(--card-bg); cursor: pointer; }
    .artifact-item.active { border-color: var(--primary-color, #007bff); box-shadow: 0 0 0 1px color-mix(in srgb, var(--primary-color, #007bff) 25%, transparent); }
    .artifact-meta { display: flex; flex-wrap: wrap; gap: 8px; font-size: 12px; color: var(--muted, #666); margin-top: 6px; }
    .artifact-upload-row { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; }
    .artifact-detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
    .artifact-section { margin-top: 14px; }
    .artifact-pre { max-height: 280px; overflow: auto; white-space: pre-wrap; word-break: break-word; }
    .artifact-pill { display: inline-flex; align-items: center; gap: 6px; border-radius: 999px; padding: 3px 10px; background: rgba(0,0,0,0.06); font-size: 12px; }
    .artifact-empty { padding: 20px; text-align: center; color: var(--muted, #666); }
    .artifact-actions { display: flex; flex-wrap: wrap; gap: 10px; }
    .artifact-stack { display: grid; gap: 10px; }
    .artifact-grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
    .artifact-preview-table { width: 100%; border-collapse: collapse; font-size: 12px; }
    .artifact-preview-table td { padding: 6px 8px; border-top: 1px solid var(--border); vertical-align: top; }
    .artifact-search-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: end; }
    .artifact-search-results { display: grid; gap: 10px; }
    @media (max-width: 980px) {
      .artifact-layout { grid-template-columns: 1fr; }
    }
  `],
  template: `
    <div class="row title-row">
      <div>
        <h2>Artifacts & Knowledge</h2>
        <p class="muted title-muted">Datei-Uploads, Extraktion, Knowledge-Links und Ergebnisartefakte des Hubs.</p>
      </div>
      <button class="secondary" (click)="refresh()" [disabled]="loadingList || uploadBusy">Aktualisieren</button>
    </div>

    <div class="card">
      <h3 class="no-margin">Datei hochladen</h3>
      <div class="artifact-upload-row mt-sm">
        <div class="flex-1">
          <label class="label-no-margin">Knowledge Collection (optional)
            <input [(ngModel)]="collectionName" placeholder="z.B. product-docs oder sprint-review" data-testid="artifact-collection-input" />
          </label>
        </div>
        <div class="flex-1">
          <label class="label-no-margin">Datei
            <input type="file" (change)="onFileSelected($event)" data-testid="artifact-file-input" />
          </label>
        </div>
        <button (click)="upload()" [disabled]="uploadBusy || !selectedFile" data-testid="artifact-upload-btn">
          {{ uploadBusy ? 'Lade hoch...' : 'Upload starten' }}
        </button>
      </div>
      @if (selectedFile) {
        <div class="artifact-meta">
          <span class="artifact-pill">{{ selectedFile.name }}</span>
          <span class="artifact-pill">{{ selectedFile.size }} bytes</span>
          <span class="artifact-pill">{{ selectedFile.type || 'unknown media type' }}</span>
        </div>
      }
    </div>

    <div class="card mt-md">
      <div class="row space-between">
        <div>
          <h3 class="no-margin">Knowledge Collections</h3>
          <p class="muted title-muted">Hub-gesteuerte Gruppierung fuer indexierte Artefakte und gezielte Knowledge-Suche.</p>
        </div>
        <button class="secondary" (click)="loadCollections()" [disabled]="loadingCollections || collectionBusy">Neu laden</button>
      </div>

      <div class="artifact-upload-row mt-sm">
        <div class="flex-1">
          <label class="label-no-margin">Neue Collection
            <input [(ngModel)]="newCollectionName" placeholder="z.B. payments-docs" />
          </label>
        </div>
        <div class="flex-1">
          <label class="label-no-margin">Beschreibung (optional)
            <input [(ngModel)]="newCollectionDescription" placeholder="Kurzbeschreibung fuer die Knowledge-Scope" />
          </label>
        </div>
        <button (click)="createCollection()" [disabled]="collectionBusy || !newCollectionName.trim()">
          {{ collectionBusy ? 'Speichere...' : 'Collection anlegen' }}
        </button>
      </div>

      @if (loadingCollections) {
        <div class="artifact-empty">Lade Collections...</div>
      } @else if (!knowledgeCollections.length) {
        <div class="artifact-empty">Noch keine Knowledge Collections vorhanden.</div>
      } @else {
        <div class="artifact-list mt-sm">
          @for (collection of knowledgeCollections; track collection.id) {
            <button class="artifact-item" [class.active]="collection.id === selectedCollectionId" (click)="selectCollection(collection.id)">
              <div class="row space-between">
                <strong>{{ collection.name }}</strong>
                <span class="badge">{{ collection.created_by || 'system' }}</span>
              </div>
              <div class="artifact-meta">
                <span>{{ collection.description || 'Keine Beschreibung' }}</span>
              </div>
            </button>
          }
        </div>
      }
    </div>

    <div class="artifact-layout mt-md">
      <div class="card">
        <div class="row space-between">
          <h3 class="no-margin">Artifacts</h3>
          <span class="badge">{{ artifacts.length }}</span>
        </div>
        @if (loadingList) {
          <div class="artifact-empty">Lade Artefakte...</div>
        } @else if (!artifacts.length) {
          <div class="artifact-empty">Noch keine Artefakte vorhanden.</div>
        } @else {
          <div class="artifact-list mt-sm">
            @for (artifact of artifacts; track artifact.id) {
              <button class="artifact-item" [class.active]="artifact.id === selectedArtifactId" (click)="selectArtifact(artifact.id)" data-testid="artifact-list-item">
                <div class="row space-between">
                  <strong>{{ artifact.latest_filename || artifact.id }}</strong>
                  <span class="badge">{{ artifact.status }}</span>
                </div>
                <div class="artifact-meta">
                  <span>{{ artifact.latest_media_type || 'unknown' }}</span>
                  <span>{{ artifact.size_bytes || 0 }} bytes</span>
                  <span>{{ artifact.created_by || 'system' }}</span>
                </div>
              </button>
            }
          </div>
        }
      </div>

      <div class="card">
        @if (loadingDetail) {
          <div class="artifact-empty">Lade Details...</div>
        } @else if (!selectedArtifact) {
          <div class="artifact-empty">Waehle links ein Artefakt aus, um Versionen, Dokumente und Knowledge-Links zu sehen.</div>
        } @else {
          <div class="row space-between">
            <div>
              <h3 class="no-margin">{{ selectedArtifact.artifact?.latest_filename || selectedArtifact.artifact?.id }}</h3>
              <div class="artifact-meta">
                <span class="artifact-pill">{{ selectedArtifact.artifact?.status }}</span>
                <span class="artifact-pill">{{ selectedArtifact.artifact?.latest_media_type || 'unknown' }}</span>
                <span class="artifact-pill">{{ selectedArtifact.artifact?.size_bytes || 0 }} bytes</span>
              </div>
            </div>
            <div class="artifact-actions">
              <button class="secondary" (click)="extractSelected()" [disabled]="extractBusy" data-testid="artifact-extract-btn">
                {{ extractBusy ? 'Extrahiere...' : 'Extraktion starten' }}
              </button>
              <button class="secondary" (click)="indexSelected()" [disabled]="indexBusy || !selectedArtifactId">
                {{ indexBusy ? 'Indexiere...' : 'RAG-Index bauen' }}
              </button>
              <button class="secondary" (click)="loadSelectedRagDetails()" [disabled]="previewBusy || !selectedArtifactId">
                {{ previewBusy ? 'Lade Preview...' : 'Preview neu laden' }}
              </button>
            </div>
          </div>

          <div class="artifact-detail-grid mt-md">
            <div class="card card-light">
              <div class="muted">Versionen</div>
              <strong>{{ selectedArtifact.versions?.length || 0 }}</strong>
            </div>
            <div class="card card-light">
              <div class="muted">Extrahierte Dokumente</div>
              <strong>{{ selectedArtifact.extracted_documents?.length || 0 }}</strong>
            </div>
            <div class="card card-light">
              <div class="muted">Knowledge-Links</div>
              <strong>{{ selectedArtifact.knowledge_links?.length || 0 }}</strong>
            </div>
            <div class="card card-light">
              <div class="muted">RAG-Status</div>
              <strong>{{ selectedArtifact.knowledge_index?.status || artifactRagStatus?.knowledge_index?.status || 'nicht indexiert' }}</strong>
            </div>
          </div>

          <div class="artifact-section">
            <h4>Knowledge Collections</h4>
            @if (knowledgeCollectionNames(selectedArtifact).length) {
              <div class="artifact-meta">
                @for (name of knowledgeCollectionNames(selectedArtifact); track name) {
                  <span class="artifact-pill">{{ name }}</span>
                }
              </div>
            } @else {
              <div class="muted">Keine Collection-Zuordnung vorhanden.</div>
            }
          </div>

          <div class="artifact-section">
            <h4>Extrahierte Inhalte</h4>
            @if (selectedArtifact.extracted_documents?.length) {
              @for (doc of selectedArtifact.extracted_documents; track doc.id) {
                <div class="card card-light mt-sm">
                  <div class="row space-between">
                    <strong>{{ doc.extraction_mode }}</strong>
                    <span class="badge">{{ doc.extraction_status }}</span>
                  </div>
                  @if (doc.text_content) {
                    <pre class="artifact-pre">{{ doc.text_content }}</pre>
                  } @else {
                    <div class="muted">Kein Textinhalt vorhanden. Nur Metadaten oder Raw-only.</div>
                  }
                </div>
              }
            } @else {
              <div class="muted">Noch keine extrahierten Dokumente vorhanden.</div>
            }
          </div>

          <div class="artifact-section">
            <h4>RAG Preview</h4>
            @if (artifactRagPreview) {
              <div class="artifact-grid-2">
                <div class="card card-light">
                  <div class="muted">Manifest</div>
                  <table class="artifact-preview-table mt-sm">
                    <tr><td>Dateien</td><td>{{ artifactRagPreview.manifest?.file_count || 0 }}</td></tr>
                    <tr><td>Index-Records</td><td>{{ artifactRagPreview.manifest?.index_record_count || 0 }}</td></tr>
                    <tr><td>Detail-Records</td><td>{{ artifactRagPreview.manifest?.detail_record_count || 0 }}</td></tr>
                    <tr><td>Relationen</td><td>{{ artifactRagPreview.manifest?.relation_record_count || 0 }}</td></tr>
                  </table>
                </div>
                <div class="card card-light">
                  <div class="muted">Index Preview</div>
                  @if (artifactRagPreview.preview?.index?.length) {
                    <div class="artifact-stack mt-sm">
                      @for (entry of artifactRagPreview.preview.index; track $index) {
                        <div>
                          <strong>{{ entry.title || entry.name || entry.kind || 'record' }}</strong>
                          <div class="muted font-sm">{{ entry.file || entry.path || 'unknown source' }}</div>
                        </div>
                      }
                    </div>
                  } @else {
                    <div class="muted mt-sm">Noch keine Preview-Daten vorhanden.</div>
                  }
                </div>
              </div>
            } @else {
              <div class="muted">Noch kein RAG-Preview geladen.</div>
            }
          </div>

          <div class="artifact-section">
            <h4>Versionen</h4>
            @if (selectedArtifact.versions?.length) {
              <div class="table-scroll">
                <table class="standard-table table-min-600">
                  <thead>
                    <tr class="card-light">
                      <th>Version</th>
                      <th>Datei</th>
                      <th>Media Type</th>
                      <th>SHA256</th>
                    </tr>
                  </thead>
                  <tbody>
                    @for (version of selectedArtifact.versions; track version.id) {
                      <tr>
                        <td>{{ version.version_number }}</td>
                        <td>{{ version.original_filename }}</td>
                        <td>{{ version.media_type }}</td>
                        <td class="font-mono font-sm">{{ version.sha256 }}</td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            } @else {
              <div class="muted">Keine Versionsdaten vorhanden.</div>
            }
          </div>
        }
      </div>
    </div>

    <div class="card mt-md">
      <div class="row space-between">
        <div>
          <h3 class="no-margin">Collection Search</h3>
          <p class="muted title-muted">Gezielte Suche ueber indexierte Artefakte einer Collection.</p>
        </div>
        @if (selectedCollectionDetail?.collection?.name) {
          <span class="artifact-pill">{{ selectedCollectionDetail.collection.name }}</span>
        }
      </div>

      <div class="artifact-search-row mt-sm">
        <div class="flex-1">
          <label class="label-no-margin">Suchanfrage
            <input [(ngModel)]="knowledgeSearchQuery" placeholder="z.B. timeout, payment flow, adapter" />
          </label>
        </div>
        <button class="secondary" (click)="indexSelectedCollection()" [disabled]="collectionIndexBusy || !selectedCollectionId">
          {{ collectionIndexBusy ? 'Indexiere...' : 'Collection indexieren' }}
        </button>
        <button (click)="searchSelectedCollection()" [disabled]="searchBusy || !selectedCollectionId || !knowledgeSearchQuery.trim()">
          {{ searchBusy ? 'Suche...' : 'Collection durchsuchen' }}
        </button>
      </div>

      @if (selectedCollectionDetail) {
        <div class="artifact-meta mt-sm">
          <span class="artifact-pill">{{ selectedCollectionDetail.knowledge_links?.length || 0 }} Links</span>
          <span class="artifact-pill">{{ selectedCollectionDetail.knowledge_indices?.length || 0 }} Indizes</span>
        </div>
      }

      @if (knowledgeSearchResults.length) {
        <div class="artifact-search-results mt-sm">
          @for (chunk of knowledgeSearchResults; track $index) {
            <div class="card card-light">
              <div class="row space-between">
                <strong>{{ chunk.source }}</strong>
                <span class="badge">{{ chunk.metadata?.record_kind || chunk.engine }}</span>
              </div>
              <div class="muted font-sm mt-5">{{ chunk.metadata?.artifact_id || 'unknown artifact' }}</div>
              <pre class="artifact-pre">{{ chunk.content }}</pre>
            </div>
          }
        </div>
      } @else {
        <div class="muted mt-sm">Noch keine Collection-Suchergebnisse vorhanden.</div>
      }
    </div>
  `,
})
export class ArtifactsComponent {
  private dir = inject(AgentDirectoryService);
  private hubApi = inject(HubApiService);
  private ns = inject(NotificationService);

  hub = this.dir.list().find((a) => a.role === 'hub');
  artifacts: any[] = [];
  selectedArtifactId: string | null = null;
  selectedArtifact: any = null;
  collectionName = '';
  newCollectionName = '';
  newCollectionDescription = '';
  selectedFile: File | null = null;
  loadingList = false;
  loadingDetail = false;
  loadingCollections = false;
  uploadBusy = false;
  extractBusy = false;
  indexBusy = false;
  previewBusy = false;
  collectionBusy = false;
  collectionIndexBusy = false;
  searchBusy = false;
  knowledgeCollections: any[] = [];
  selectedCollectionId: string | null = null;
  selectedCollectionDetail: any = null;
  artifactRagStatus: any = null;
  artifactRagPreview: any = null;
  knowledgeSearchQuery = '';
  knowledgeSearchResults: any[] = [];

  constructor() {
    this.refresh();
    this.loadCollections();
  }

  refresh() {
    if (!this.hub) return;
    this.loadingList = true;
    this.hubApi.listArtifacts(this.hub.url).pipe(
      finalize(() => {
        this.loadingList = false;
      }),
    ).subscribe({
      next: (items) => {
        this.artifacts = Array.isArray(items) ? items : [];
        if (!this.selectedArtifactId && this.artifacts.length) {
          this.selectArtifact(this.artifacts[0].id);
          return;
        }
        if (this.selectedArtifactId) {
          const stillExists = this.artifacts.some((item) => item.id === this.selectedArtifactId);
          if (stillExists) {
            this.selectArtifact(this.selectedArtifactId);
          } else {
            this.selectedArtifactId = null;
            this.selectedArtifact = null;
          }
        }
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Artefakte konnten nicht geladen werden')),
    });
  }

  loadCollections() {
    if (!this.hub) return;
    this.loadingCollections = true;
    this.hubApi.listKnowledgeCollections(this.hub.url).pipe(
      finalize(() => {
        this.loadingCollections = false;
      }),
    ).subscribe({
      next: (items) => {
        this.knowledgeCollections = Array.isArray(items) ? items : [];
        if (!this.selectedCollectionId && this.knowledgeCollections.length) {
          this.selectCollection(this.knowledgeCollections[0].id);
        }
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Collections konnten nicht geladen werden')),
    });
  }

  onFileSelected(event: Event) {
    const input = event.target as HTMLInputElement | null;
    this.selectedFile = input?.files?.[0] || null;
  }

  createCollection() {
    if (!this.hub || !this.newCollectionName.trim()) return;
    this.collectionBusy = true;
    this.hubApi.createKnowledgeCollection(this.hub.url, {
      name: this.newCollectionName.trim(),
      description: this.newCollectionDescription.trim() || undefined,
    }).pipe(
      finalize(() => {
        this.collectionBusy = false;
      }),
    ).subscribe({
      next: (collection) => {
        this.ns.success('Collection angelegt');
        this.newCollectionName = '';
        this.newCollectionDescription = '';
        this.loadCollections();
        if (collection?.id) {
          this.selectCollection(collection.id);
        }
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Collection konnte nicht angelegt werden')),
    });
  }

  upload() {
    if (!this.hub || !this.selectedFile) return;
    this.uploadBusy = true;
    this.hubApi.uploadArtifact(this.hub.url, this.selectedFile, this.collectionName).pipe(
      finalize(() => {
        this.uploadBusy = false;
      }),
    ).subscribe({
      next: (result) => {
        const artifactId = result?.artifact?.id || null;
        this.ns.success('Artefakt hochgeladen');
        this.selectedFile = null;
        this.collectionName = '';
        this.refresh();
        if (artifactId) {
          this.selectedArtifactId = artifactId;
          this.selectArtifact(artifactId);
        }
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Upload fehlgeschlagen')),
    });
  }

  selectArtifact(artifactId: string) {
    if (!this.hub || !artifactId) return;
    this.selectedArtifactId = artifactId;
    this.loadingDetail = true;
    this.hubApi.getArtifact(this.hub.url, artifactId).pipe(
      finalize(() => {
        this.loadingDetail = false;
      }),
    ).subscribe({
      next: (payload) => {
        this.selectedArtifact = payload;
        this.artifactRagStatus = null;
        this.artifactRagPreview = null;
        this.loadSelectedRagDetails();
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Artifact-Details konnten nicht geladen werden')),
    });
  }

  extractSelected() {
    if (!this.hub || !this.selectedArtifactId) return;
    this.extractBusy = true;
    this.hubApi.extractArtifact(this.hub.url, this.selectedArtifactId).pipe(
      finalize(() => {
        this.extractBusy = false;
      }),
    ).subscribe({
      next: () => {
        this.ns.success('Extraktion gestartet');
        this.selectArtifact(this.selectedArtifactId!);
        this.refresh();
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Extraktion fehlgeschlagen')),
    });
  }

  indexSelected() {
    if (!this.hub || !this.selectedArtifactId) return;
    this.indexBusy = true;
    this.hubApi.indexArtifact(this.hub.url, this.selectedArtifactId).pipe(
      finalize(() => {
        this.indexBusy = false;
      }),
    ).subscribe({
      next: () => {
        this.ns.success('RAG-Index erstellt');
        this.selectArtifact(this.selectedArtifactId!);
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'RAG-Index fehlgeschlagen')),
    });
  }

  loadSelectedRagDetails() {
    if (!this.hub || !this.selectedArtifactId) return;
    this.previewBusy = true;
    this.hubApi.getArtifactRagStatus(this.hub.url, this.selectedArtifactId).subscribe({
      next: (payload) => {
        this.artifactRagStatus = payload;
      },
      error: () => {
        this.artifactRagStatus = null;
      },
    });
    this.hubApi.getArtifactRagPreview(this.hub.url, this.selectedArtifactId, 5).pipe(
      finalize(() => {
        this.previewBusy = false;
      }),
    ).subscribe({
      next: (payload) => {
        this.artifactRagPreview = payload;
      },
      error: () => {
        this.artifactRagPreview = null;
      },
    });
  }

  selectCollection(collectionId: string) {
    if (!this.hub || !collectionId) return;
    this.selectedCollectionId = collectionId;
    this.hubApi.getKnowledgeCollection(this.hub.url, collectionId).subscribe({
      next: (payload) => {
        this.selectedCollectionDetail = payload;
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Collection-Details konnten nicht geladen werden')),
    });
  }

  indexSelectedCollection() {
    if (!this.hub || !this.selectedCollectionId) return;
    this.collectionIndexBusy = true;
    this.hubApi.indexKnowledgeCollection(this.hub.url, this.selectedCollectionId).pipe(
      finalize(() => {
        this.collectionIndexBusy = false;
      }),
    ).subscribe({
      next: () => {
        this.ns.success('Collection indexiert');
        this.selectCollection(this.selectedCollectionId!);
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Collection-Index fehlgeschlagen')),
    });
  }

  searchSelectedCollection() {
    if (!this.hub || !this.selectedCollectionId || !this.knowledgeSearchQuery.trim()) return;
    this.searchBusy = true;
    this.hubApi.searchKnowledgeCollection(this.hub.url, this.selectedCollectionId, {
      query: this.knowledgeSearchQuery.trim(),
      top_k: 5,
    }).pipe(
      finalize(() => {
        this.searchBusy = false;
      }),
    ).subscribe({
      next: (payload) => {
        this.knowledgeSearchResults = Array.isArray(payload?.chunks) ? payload.chunks : [];
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Collection-Suche fehlgeschlagen')),
    });
  }

  knowledgeCollectionNames(payload: any): string[] {
    const links = Array.isArray(payload?.knowledge_links) ? payload.knowledge_links : [];
    const names = links
      .map((link: any) => String(link?.link_metadata?.collection_name || link?.collection_id || '').trim())
      .filter((value: string) => !!value);
    return Array.from(new Set(names));
  }
}
