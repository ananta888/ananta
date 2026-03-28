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
            <button class="secondary" (click)="extractSelected()" [disabled]="extractBusy" data-testid="artifact-extract-btn">
              {{ extractBusy ? 'Extrahiere...' : 'Extraktion starten' }}
            </button>
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
  selectedFile: File | null = null;
  loadingList = false;
  loadingDetail = false;
  uploadBusy = false;
  extractBusy = false;

  constructor() {
    this.refresh();
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

  onFileSelected(event: Event) {
    const input = event.target as HTMLInputElement | null;
    this.selectedFile = input?.files?.[0] || null;
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

  knowledgeCollectionNames(payload: any): string[] {
    const links = Array.isArray(payload?.knowledge_links) ? payload.knowledge_links : [];
    const names = links
      .map((link: any) => String(link?.link_metadata?.collection_name || link?.collection_id || '').trim())
      .filter((value: string) => !!value);
    return Array.from(new Set(names));
  }
}
