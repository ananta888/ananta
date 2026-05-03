import { Component, OnDestroy, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { UiSkeletonComponent } from './ui-skeleton.component';
import { AdminFacade } from '../features/admin/admin.facade';
import { AgentApiService } from '../services/agent-api.service';
import { decisionExplanation, userFacingTerm } from '../models/user-facing-language';
import { SummaryMetric, SummaryPanelComponent, TableShellComponent } from '../shared/ui/display';

@Component({
  standalone: true,
  selector: 'app-artifacts',
  imports: [CommonModule, FormsModule, UiSkeletonComponent, SummaryPanelComponent, TableShellComponent],
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
    .artifact-profile-card { border: 1px dashed var(--border); border-radius: 10px; padding: 10px; background: color-mix(in srgb, var(--card-bg) 88%, white); }
    .artifact-flow-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
    .artifact-flow-group { border: 1px solid var(--border); border-radius: 10px; padding: 12px; background: var(--card-bg); }
    .artifact-flow-artifacts { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
    .artifact-flow-files { display: grid; gap: 6px; margin-top: 8px; max-height: 200px; overflow: auto; }
    .artifact-file { justify-content: flex-start; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
    .workspace-inspector-controls { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; }
    .workspace-tree { margin-top: 10px; max-height: 340px; overflow: auto; border: 1px solid var(--border); border-radius: 10px; background: color-mix(in srgb, var(--card-bg) 92%, white); }
    .workspace-tree-line { display: flex; align-items: center; gap: 10px; font-size: 12px; padding: 4px 8px; border-top: 1px solid color-mix(in srgb, var(--border) 55%, transparent); }
    .workspace-tree-line:first-child { border-top: none; }
    .workspace-tree-line.dir { font-weight: 600; }
    .workspace-tree-name { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
    .workspace-tree-meta { margin-left: auto; color: var(--muted, #666); font-size: 11px; }
    @media (max-width: 980px) {
      .artifact-layout { grid-template-columns: 1fr; }
    }
  `],
  template: `
    <div class="row title-row">
      <div>
        <h2>Ergebnisse & Wissen</h2>
        <p class="muted title-muted">{{ term('artifact').technicalLabel }} bedeutet hier: {{ term('artifact').hint }}</p>
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
          <p class="muted title-muted">Sammlungen gruppieren Ergebnisse, damit du gezielt in ihnen suchen kannst.</p>
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

      <div class="artifact-upload-row mt-sm">
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
          <div class="artifact-profile-card">
            <strong>{{ activeCollectionProfile()?.label || 'Kein Profil' }}</strong>
            <div class="muted font-sm mt-5">{{ activeCollectionProfile()?.description || 'Kein Profil geladen.' }}</div>
          </div>
        </div>
      </div>

      @if (loadingCollections) {
        <app-ui-skeleton [count]="1" [lineCount]="4"></app-ui-skeleton>
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

    <div class="card mt-md">
      <div class="row space-between">
        <div>
          <h3 class="no-margin">Wikipedia als lokale RAG-Quelle</h3>
          <p class="muted title-muted">Echten Wikimedia-Dump waehlen oder eigene XML/BZ2/ZIM-URL angeben. XML-Dumps werden lokal normalisiert und indexiert.</p>
        </div>
        <button class="secondary" (click)="loadWikiPresets()" [disabled]="loadingWikiPresets || wikiImportBusy">Presets neu laden</button>
      </div>

      <div class="artifact-upload-row mt-sm">
        <div class="flex-1">
          <label class="label-no-margin">Preset
            <select [(ngModel)]="selectedWikiPresetId" (ngModelChange)="onWikiPresetChanged()">
              <option value="">Eigene Quelle (URL)</option>
              @for (preset of wikiPresets; track preset.id) {
                <option [value]="preset.id" [disabled]="preset.supported === false">{{ preset.label }}</option>
              }
            </select>
          </label>
        </div>
        <div class="flex-1">
          <label class="label-no-margin">Dump URL
            <input [(ngModel)]="wikiCorpusUrl" placeholder="https://.../pages-articles-multistream.xml.bz2 oder .zim" [disabled]="!!selectedWikiPresetId" />
          </label>
        </div>
      </div>

      <div class="artifact-upload-row mt-sm">
        <div class="flex-1">
          <label class="label-no-margin">Source ID
            <input [(ngModel)]="wikiSourceId" placeholder="z.B. wikipedia-de-multistream-latest" />
          </label>
        </div>
        <div class="flex-1">
          <label class="label-no-margin">Sprache
            <input [(ngModel)]="wikiLanguage" placeholder="en/de/..." />
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
      </div>

      <div class="artifact-upload-row mt-sm">
        <label class="checkbox-inline">
          <input type="checkbox" [(ngModel)]="wikiCodeCompassPrerender" />
          CodeCompass Vor-Rendering verwenden
        </label>
        <label class="checkbox-inline">
          <input type="checkbox" [(ngModel)]="wikiStrict" />
          Strikter Import (fehlerhafte Zeilen abbrechen)
        </label>
        <button (click)="importWiki()" [disabled]="wikiImportBusy || !canImportWiki()">
          {{ wikiImportBusy ? 'Importiere...' : 'Wikipedia importieren' }}
        </button>
      </div>

      @if (selectedWikiPreset()) {
        <div class="artifact-meta mt-sm">
          <span class="artifact-pill">{{ selectedWikiPreset()?.description || selectedWikiPreset()?.label }}</span>
          <span class="artifact-pill">{{ selectedWikiPreset()?.size_hint || 'Groesse unbekannt' }}</span>
          @if (selectedWikiPreset()?.index_url) {
            <span class="artifact-pill">Multistream-Index vorhanden</span>
          }
          @if (selectedWikiPreset()?.supported === false) {
            <span class="artifact-pill">Prototyp: Parser noch nicht aktiv</span>
          }
        </div>
      }

      @if (wikiImportResult) {
        <div class="artifact-meta mt-sm">
          <span class="artifact-pill">Source: {{ wikiImportResult?.source_id || wikiSourceId || 'wiki' }}</span>
          <span class="artifact-pill">Records: {{ wikiImportResult?.stats?.normalized_records || wikiImportResult?.stats?.records_total || 0 }}</span>
          <span class="artifact-pill">Issues: {{ wikiImportResult?.issues?.length || 0 }}</span>
        </div>
      }
      @if (wikiImportJobId || wikiImportJob) {
        <div class="artifact-meta mt-sm">
          <span class="artifact-pill">Job: {{ wikiImportJobId || wikiImportJob?.job_id || 'n/a' }}</span>
          <span class="artifact-pill">Status: {{ wikiImportJob?.status || (wikiImportBusy ? 'running' : 'unknown') }}</span>
          <span class="artifact-pill">Phase: {{ wikiImportJob?.phase || (wikiImportBusy ? 'indexing' : 'n/a') }}</span>
          <span class="artifact-pill">Progress: {{ wikiImportJob?.progress_percent ?? (wikiImportBusy ? 10 : 0) }}%</span>
        </div>
      }
    </div>

    <div class="card mt-md">
      <div class="row space-between">
        <div>
          <h3 class="no-margin">Execution Artifact Explorer</h3>
          <p class="muted title-muted">Transparente Sicht auf Ergebnisse pro Aufgabe, Worker und Vorlage. {{ decisionExplanation('routing') }}</p>
        </div>
        <button class="secondary" (click)="loadArtifactFlow()" [disabled]="loadingArtifactFlow">Neu laden</button>
      </div>

      @if (loadingArtifactFlow) {
        <app-ui-skeleton [count]="2" [lineCount]="4"></app-ui-skeleton>
      } @else if (!artifactFlowItems().length) {
        <div class="artifact-empty">Noch keine Ausfuehrungs-Artefakte im Orchestrierungsmodell vorhanden.</div>
      } @else {
        <div class="artifact-flow-grid mt-sm">
          <div class="artifact-flow-group">
            <strong>Nach Task</strong>
            <div class="artifact-stack mt-sm">
              @for (item of artifactFlowItems(); track item.item_id || item.task_id || $index) {
                <div class="card card-light">
                  <div class="row space-between">
                    <strong>{{ item.task_title || item.task_id || 'Task' }}</strong>
                    <span class="badge">{{ artifactCount(item) }} Artefakte</span>
                  </div>
                  <div class="muted font-sm mt-5">
                    {{ item.assignment?.agent_name || item.worker_name || item.worker_url || 'Unbekannter Worker' }}
                    @if (item.assignment?.template_name) {
                      · {{ item.assignment.template_name }}
                    }
                  </div>
                  <div class="artifact-flow-artifacts">
                    @for (artifact of itemArtifacts(item); track artifact.artifact_id) {
                      <button class="artifact-pill" (click)="selectArtifactBySummary(artifact)">{{ artifact.label || artifact.artifact_id }}</button>
                    }
                  </div>
                  @if (itemWorkspaceFiles(item).length) {
                    <div class="mt-sm">
                      <div class="muted font-sm">Workspace-Dateien ({{ itemWorkspaceFiles(item).length }})</div>
                      <div class="artifact-flow-files">
                        @for (file of itemWorkspaceFiles(item); track file.workspace_relative_path + '-' + (file.worker_job_id || '') + '-' + (file.artifact_id || '')) {
                          <button class="artifact-pill artifact-file" (click)="selectArtifactBySummary(file)">
                            {{ file.workspace_relative_path }}
                          </button>
                        }
                      </div>
                    </div>
                  }
                </div>
              }
            </div>
          </div>

          <div class="artifact-flow-group">
            <strong>Nach Worker</strong>
            <div class="artifact-stack mt-sm">
              @for (group of artifactFlowWorkerGroups(); track group.worker_url || group.worker_name || $index) {
                <div class="card card-light">
                  <div class="row space-between">
                    <strong>{{ group.worker_name || group.worker_url || 'Worker' }}</strong>
                    <span class="badge">{{ (group.artifact_ids || []).length }} Artefakte</span>
                  </div>
                  <div class="artifact-flow-artifacts">
                    @for (artifact of groupArtifacts(group); track artifact.artifact_id) {
                      <button class="artifact-pill" (click)="selectArtifactBySummary(artifact)">{{ artifact.label || artifact.artifact_id }}</button>
                    }
                  </div>
                  @if (workerWorkspaceFiles(group).length) {
                    <div class="mt-sm">
                      <div class="muted font-sm">Workspace-Dateien ({{ workerWorkspaceFiles(group).length }})</div>
                      <div class="artifact-flow-files">
                        @for (file of workerWorkspaceFiles(group); track file.workspace_relative_path + '-' + (file.worker_job_id || '') + '-' + (file.artifact_id || '')) {
                          <button class="artifact-pill artifact-file" (click)="selectArtifactBySummary(file)">
                            {{ file.workspace_relative_path }}
                          </button>
                        }
                      </div>
                    </div>
                  }
                </div>
              }
            </div>
          </div>

          <div class="artifact-flow-group">
            <strong>Nach Agent / Template</strong>
            <div class="artifact-stack mt-sm">
              @for (group of artifactFlowAssignmentGroups(); track group.assignment_key || $index) {
                <div class="card card-light">
                  <div class="row space-between">
                    <strong>{{ assignmentLabel(group) }}</strong>
                    <span class="badge">{{ (group.artifact_ids || []).length }} Artefakte</span>
                  </div>
                  <div class="muted font-sm mt-5">{{ group.role_name || 'Ohne Rolle' }}</div>
                  <div class="artifact-flow-artifacts">
                    @for (artifact of groupArtifacts(group); track artifact.artifact_id) {
                      <button class="artifact-pill" (click)="selectArtifactBySummary(artifact)">{{ artifact.label || artifact.artifact_id }}</button>
                    }
                  </div>
                </div>
              }
            </div>
          </div>
        </div>

        <div class="card card-light mt-md">
          <div class="row space-between">
            <strong>Live Worker Workspace Explorer</strong>
            @if (workspaceInspectorMeta()) {
              <span class="badge">{{ workspaceInspectorMeta()?.file_count || 0 }} Dateien</span>
            }
          </div>
          @if (!workspaceCandidates().length) {
            <div class="muted mt-sm">Keine Worker-Runs mit Subtask-ID verfuegbar.</div>
          } @else {
            <div class="workspace-inspector-controls mt-sm">
              <label class="label-no-margin">
                <span class="muted font-sm">Worker-Run</span>
                <select
                  [ngModel]="selectedWorkspaceRunKey"
                  (ngModelChange)="selectedWorkspaceRunKey = $event; workspaceSelectionChanged()"
                >
                  @for (candidate of workspaceCandidates(); track candidate.key) {
                    <option [ngValue]="candidate.key">{{ workspaceRunLabel(candidate) }}</option>
                  }
                </select>
              </label>
              <label class="label-no-margin">
                <span class="muted font-sm">Ansicht</span>
                <div class="row gap-sm mt-5">
                  <input
                    type="checkbox"
                    [ngModel]="workspaceTrackedOnly"
                    (ngModelChange)="workspaceTrackedOnly = !!$event; workspaceSelectionChanged()"
                  />
                  <span>Nur Projektdateien zeigen (ohne interne Ergebnisablage)</span>
                </div>
              </label>
              <button class="secondary" (click)="loadSelectedWorkspaceFiles()" [disabled]="workspaceLoading">
                {{ workspaceLoading ? 'Lade...' : 'Workspace laden' }}
              </button>
            </div>
            @if (workspaceLoadError) {
              <div class="muted mt-sm danger">{{ workspaceLoadError }}</div>
            }
            @if (workspaceInspectorMeta()) {
              <div class="muted font-sm mt-sm">
                {{ workspaceInspectorMeta()?.workspace_dir }}
                @if (workspaceInspectorMeta()?.truncated) {
                  · gekuerzt auf {{ workspaceInspectorMeta()?.max_entries || '-' }} Eintraege
                }
              </div>
            }
            @if (workspaceTreeLines().length) {
              <div class="workspace-tree">
                @for (line of workspaceTreeLines(); track line.type + ':' + line.path) {
                  <div class="workspace-tree-line" [class.dir]="line.type === 'dir'" [style.paddingLeft.px]="8 + (line.depth * 16)">
                    <span class="workspace-tree-name">{{ line.name }}</span>
                    @if (line.type === 'file') {
                      <span class="workspace-tree-meta">{{ line.size_bytes || 0 }} B</span>
                    }
                  </div>
                }
              </div>
            } @else if (!workspaceLoading && workspaceInspectorMeta()) {
              <div class="muted mt-sm">Keine Dateien im aktuellen Workspace gefunden.</div>
            }
          }
        </div>
      }
    </div>

    <div class="artifact-layout mt-md">
      <div class="card">
        <div class="row space-between">
          <h3 class="no-margin">Ergebnisse</h3>
          <span class="badge">{{ artifacts.length }}</span>
        </div>
        @if (loadingList) {
          <app-ui-skeleton [count]="3" [lineCount]="3"></app-ui-skeleton>
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
          <app-ui-skeleton [count]="1" [lineCount]="6"></app-ui-skeleton>
        } @else if (!selectedArtifact) {
          <div class="artifact-empty">Waehle links ein Ergebnis aus, um Versionen, Dokumente und Wissenslinks zu sehen.</div>
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
              <label class="label-no-margin">
                <span class="muted font-sm">Profil</span>
                <select [(ngModel)]="selectedArtifactProfileName">
                  @for (profile of knowledgeProfiles; track profile.name) {
                    <option [value]="profile.name">{{ profile.label }}</option>
                  }
                </select>
              </label>
              <button class="secondary" (click)="indexSelected()" [disabled]="indexBusy || !selectedArtifactId">
                {{ indexBusy ? 'Indexiere...' : 'RAG-Index bauen' }}
              </button>
              <button class="secondary" (click)="loadSelectedRagDetails()" [disabled]="previewBusy || !selectedArtifactId">
                {{ previewBusy ? 'Lade Preview...' : 'Preview neu laden' }}
              </button>
            </div>
          </div>

          <app-summary-panel
            class="block mt-md"
            title="Artifact Summary"
            summary="Kompakte Sicht auf Versionen, Extraktion, Wissenslinks und RAG-Index."
            [metrics]="selectedArtifactSummaryMetrics()"
            [columns]="3"
          ></app-summary-panel>

          <div class="artifact-section">
            <h4>Wissenssammlungen</h4>
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
                    <tr><td>Profil</td><td>{{ artifactRagPreview.knowledge_index?.profile_name || 'default' }}</td></tr>
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
            <app-table-shell
              title="Versionen"
              subtitle="Alle gespeicherten Versionen dieses Artefakts."
              [empty]="!selectedArtifact.versions?.length"
              emptyTitle="Keine Versionsdaten vorhanden"
            >
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
            </app-table-shell>
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
        <label class="label-no-margin">
          <span class="muted font-sm">Profil</span>
          <select [(ngModel)]="selectedCollectionProfileName">
            @for (profile of knowledgeProfiles; track profile.name) {
              <option [value]="profile.name">{{ profile.label }}</option>
            }
          </select>
        </label>
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
export class ArtifactsComponent implements OnDestroy {
  private dir = inject(AgentDirectoryService);
  private hubApi = inject(AdminFacade);
  private ns = inject(NotificationService);
  private agentApi = inject(AgentApiService);

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
  knowledgeProfiles: any[] = [];
  selectedCollectionId: string | null = null;
  selectedCollectionDetail: any = null;
  artifactRagStatus: any = null;
  artifactRagPreview: any = null;
  knowledgeSearchQuery = '';
  knowledgeSearchResults: any[] = [];
  selectedArtifactProfileName = 'default';
  selectedCollectionProfileName = 'default';
  readonly fallbackWikiPresets: any[] = [
    {
      id: 'wikipedia-de-multistream-latest',
      label: 'Wikipedia DE: Artikel Multistream (latest)',
      description: 'Empfohlen fuer ernsthaftes deutsches RAG: echter Wikimedia XML.BZ2 Multistream-Dump plus Index.',
      corpus_url: 'https://dumps.wikimedia.org/dewiki/latest/dewiki-latest-pages-articles-multistream.xml.bz2',
      index_url: 'https://dumps.wikimedia.org/dewiki/latest/dewiki-latest-pages-articles-multistream-index.txt.bz2',
      source_id: 'wikipedia-de-multistream-latest',
      language: 'de',
      size_hint: '~8.1 GB dump + ~63 MB index',
      recommended: true,
      import_format: 'mediawiki-multistream',
      codecompass_prerender: true,
    },
    {
      id: 'wikipedia-de-pages-latest',
      label: 'Wikipedia DE: Artikel nicht-Multistream (latest)',
      description: 'Fallback ohne Multistream-Index; meist weniger praktisch fuer grosse lokale Verarbeitung.',
      corpus_url: 'https://dumps.wikimedia.org/dewiki/latest/dewiki-latest-pages-articles.xml.bz2',
      source_id: 'wikipedia-de-pages-latest',
      language: 'de',
      size_hint: '~7.8 GB',
      recommended: false,
      import_format: 'mediawiki-xml',
      codecompass_prerender: true,
    },
    {
      id: 'wikipedia-de-zim-mini-2026-04',
      label: 'Wikipedia DE: ZIM mini 2026-04 (Prototyp)',
      description: 'Kleiner Kiwix/ZIM-Prototyp-Dump. Sichtbar fuer Download-Planung; Import benoetigt noch ZIM-Parser.',
      corpus_url: 'https://dumps.wikimedia.org/kiwix/zim/wikipedia/wikipedia_de_all_mini_2026-04.zim',
      source_id: 'wikipedia-de-zim-mini-2026-04',
      language: 'de',
      size_hint: '~3.9 GiB',
      recommended: false,
      import_format: 'zim',
      supported: false,
      codecompass_prerender: false,
    },
    {
      id: 'wikipedia-de-zim-nopic-2026-01',
      label: 'Wikipedia DE: ZIM ohne Bilder 2026-01 (Prototyp)',
      description: 'Groesserer Kiwix/ZIM-Dump ohne Bilder. Sichtbar fuer spaetere ZIM-Unterstuetzung.',
      corpus_url: 'https://dumps.wikimedia.org/kiwix/zim/wikipedia/wikipedia_de_all_nopic_2026-01.zim',
      source_id: 'wikipedia-de-zim-nopic-2026-01',
      language: 'de',
      size_hint: '~13.6 GiB',
      recommended: false,
      import_format: 'zim',
      supported: false,
      codecompass_prerender: false,
    },
  ];
  wikiPresets: any[] = [];
  loadingWikiPresets = false;
  selectedWikiPresetId = '';
  wikiCorpusUrl = '';
  wikiSourceId = '';
  wikiLanguage = 'en';
  wikiStrict = false;
  wikiCodeCompassPrerender = true;
  wikiImportBusy = false;
  wikiImportResult: any = null;
  wikiImportJobId = '';
  wikiImportJob: any = null;
  private wikiImportPollTimer: ReturnType<typeof setTimeout> | null = null;
  artifactFlowReadModel: any = null;
  loadingArtifactFlow = false;
  selectedWorkspaceRunKey = '';
  workspaceTrackedOnly = true;
  workspaceLoading = false;
  workspaceLoadError = '';
  workspaceFilePayload: any = null;
  workspaceTreeLineItems: any[] = [];
  term = userFacingTerm;
  decisionExplanation = decisionExplanation;

  constructor() {
    this.applyWikiPresets([]);
    this.refresh();
    this.loadCollections();
    this.loadProfiles();
    this.loadWikiPresets();
    this.loadArtifactFlow();
  }

  ngOnDestroy(): void {
    this.stopWikiImportPolling();
  }

  selectedArtifactSummaryMetrics(): SummaryMetric[] {
    return [
      { label: 'Versionen', value: this.selectedArtifact?.versions?.length || 0 },
      { label: 'Extrahierte Dokumente', value: this.selectedArtifact?.extracted_documents?.length || 0 },
      { label: 'Wissenslinks', value: this.selectedArtifact?.knowledge_links?.length || 0 },
      {
        label: 'RAG-Status',
        value: this.selectedArtifact?.knowledge_index?.status || this.artifactRagStatus?.knowledge_index?.status || 'nicht indexiert',
      },
      {
        label: 'Index-Profil',
        value: this.selectedArtifact?.knowledge_index?.profile_name || this.artifactRagStatus?.knowledge_index?.profile_name || 'default',
      },
    ];
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
    this.loadArtifactFlow();
  }

  loadArtifactFlow() {
    if (!this.hub) return;
    this.loadingArtifactFlow = true;
    this.hubApi.getTaskOrchestrationReadModel(this.hub.url).pipe(
      finalize(() => {
        this.loadingArtifactFlow = false;
      }),
    ).subscribe({
      next: (payload) => {
        this.artifactFlowReadModel = payload?.artifact_flow || null;
        this.ensureWorkspaceSelection();
      },
      error: (error) => {
        this.artifactFlowReadModel = null;
        this.selectedWorkspaceRunKey = '';
        this.workspaceFilePayload = null;
        this.workspaceTreeLineItems = [];
        this.ns.error(this.ns.fromApiError(error, 'Artefakt-Fluss konnte nicht geladen werden'));
      },
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

  loadProfiles() {
    if (!this.hub) return;
    this.hubApi.listKnowledgeIndexProfiles(this.hub.url).subscribe({
      next: (payload) => {
        const items = Array.isArray(payload?.items) ? payload.items : [];
        this.knowledgeProfiles = items;
        const defaultProfile = items.find((item: any) => item?.is_default)?.name || items[0]?.name || 'default';
        if (!this.selectedArtifactProfileName) this.selectedArtifactProfileName = defaultProfile;
        if (!this.selectedCollectionProfileName) this.selectedCollectionProfileName = defaultProfile;
      },
      error: () => {
        this.knowledgeProfiles = [];
      },
    });
  }

  loadWikiPresets() {
    if (!this.hub) return;
    this.loadingWikiPresets = true;
    this.hubApi.listWikiPresets(this.hub.url).pipe(
      finalize(() => {
        this.loadingWikiPresets = false;
      }),
    ).subscribe({
      next: (payload) => {
        const items = Array.isArray(payload?.items) ? payload.items : [];
        this.applyWikiPresets(items);
      },
      error: () => {
        this.applyWikiPresets([]);
      },
    });
  }

  private applyWikiPresets(items: any[]) {
    const remote = Array.isArray(items) ? items : [];
    const merged = [...remote, ...this.fallbackWikiPresets];
    const deduped: any[] = [];
    const seen = new Set<string>();
    for (const item of merged) {
      const id = String(item?.id || '').trim();
      if (!id || seen.has(id)) continue;
      seen.add(id);
      deduped.push(item);
    }
    const effective = deduped.length ? deduped : this.fallbackWikiPresets;
    this.wikiPresets = effective;
    if (!this.selectedWikiPresetId && effective.length) {
      const recommended = effective.find((item: any) => !!item?.recommended) || effective[0];
      this.selectedWikiPresetId = String(recommended?.id || '').trim();
      this.onWikiPresetChanged();
    }
  }

  selectedWikiPreset(): any | null {
    if (!this.selectedWikiPresetId) return null;
    return this.wikiPresets.find((item: any) => String(item?.id || '') === this.selectedWikiPresetId) || null;
  }

  onWikiPresetChanged() {
    const preset = this.selectedWikiPreset();
    if (!preset) return;
    this.wikiCorpusUrl = String(preset?.corpus_url || '').trim();
    this.wikiSourceId = String(preset?.source_id || '').trim();
    this.wikiLanguage = String(preset?.language || 'en').trim() || 'en';
    this.wikiCodeCompassPrerender = Boolean(preset?.codecompass_prerender);
  }

  canImportWiki(): boolean {
    const preset = this.selectedWikiPreset();
    if (preset?.supported === false) return false;
    if (this.selectedWikiPresetId) return true;
    return !!this.wikiCorpusUrl.trim();
  }

  importWiki() {
    if (!this.hub || !this.canImportWiki()) return;
    const payload: any = {
      profile_name: this.selectedCollectionProfileName || 'default',
      language: (this.wikiLanguage || 'en').trim().toLowerCase() || 'en',
      strict: this.wikiStrict,
      codecompass_prerender: this.wikiCodeCompassPrerender,
      async: true,
      source_metadata: {
        imported_from: 'artifacts_component',
      },
    };
    const preset = this.selectedWikiPreset();
    if (preset && String(preset?.corpus_url || '').trim()) {
      payload.corpus_url = String(preset.corpus_url).trim();
      if (String(preset?.index_url || '').trim()) {
        payload.index_url = String(preset.index_url).trim();
      }
    } else {
      payload.corpus_url = this.wikiCorpusUrl.trim();
    }
    if (this.wikiSourceId.trim()) {
      payload.source_id = this.wikiSourceId.trim();
    }
    this.wikiImportBusy = true;
    this.wikiImportResult = null;
    this.wikiImportJob = null;
    this.wikiImportJobId = '';
    this.stopWikiImportPolling();
    this.hubApi.importWikiFromUrl(this.hub.url, payload).pipe(
      finalize(() => {
        if (!this.wikiImportJobId) {
          this.wikiImportBusy = false;
        }
      }),
    ).subscribe({
      next: (response) => {
        this.wikiImportResult = response?.import_report || null;
        const jobId = String(response?.job?.job_id || response?.job?.id || '').trim();
        if (jobId) {
          this.wikiImportJobId = jobId;
          this.wikiImportJob = response?.job || null;
          this.wikiImportBusy = true;
          this.ns.success(`Wikipedia-Import gestartet (Job ${jobId})`);
          this.startWikiImportPolling(jobId);
          return;
        }
        this.wikiImportBusy = false;
        this.ns.success('Wikipedia-Import abgeschlossen');
        this.loadCollections();
      },
      error: (error) => {
        this.wikiImportBusy = false;
        this.stopWikiImportPolling();
        this.ns.error(this.ns.fromApiError(error, 'Wikipedia-Import fehlgeschlagen'));
      },
    });
  }

  private startWikiImportPolling(jobId: string): void {
    if (!this.hub) return;
    this.stopWikiImportPolling();
    const poll = () => {
      if (!this.hub) return;
      this.hubApi.getWikiImportJob(this.hub.url, jobId).subscribe({
        next: (response) => {
          const job = response?.job || null;
          this.wikiImportJob = job;
          const status = String(job?.status || '').trim().toLowerCase();
          if (status === 'completed') {
            this.wikiImportBusy = false;
            this.stopWikiImportPolling();
            this.ns.success('Wikipedia-Import abgeschlossen');
            this.loadCollections();
            return;
          }
          if (status === 'failed' || status === 'cancelled') {
            this.wikiImportBusy = false;
            this.stopWikiImportPolling();
            const errorHint = String(job?.error || '').trim();
            this.ns.error(errorHint ? `Wikipedia-Import fehlgeschlagen: ${errorHint}` : 'Wikipedia-Import fehlgeschlagen');
            return;
          }
          this.wikiImportPollTimer = setTimeout(poll, 2000);
        },
        error: () => {
          this.wikiImportPollTimer = setTimeout(poll, 4000);
        },
      });
    };
    this.wikiImportPollTimer = setTimeout(poll, 800);
  }

  private stopWikiImportPolling(): void {
    if (this.wikiImportPollTimer) {
      clearTimeout(this.wikiImportPollTimer);
      this.wikiImportPollTimer = null;
    }
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
    this.hubApi.indexArtifact(this.hub.url, this.selectedArtifactId, {
      profile_name: this.selectedArtifactProfileName || 'default',
    }).pipe(
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
    this.hubApi.indexKnowledgeCollection(this.hub.url, this.selectedCollectionId, {
      profile_name: this.selectedCollectionProfileName || 'default',
    }).pipe(
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

  activeCollectionProfile(): any {
    return this.knowledgeProfiles.find((profile: any) => profile?.name === this.selectedCollectionProfileName) || null;
  }

  artifactFlowItems(): any[] {
    return Array.isArray(this.artifactFlowReadModel?.items) ? this.artifactFlowReadModel.items : [];
  }

  artifactFlowWorkerGroups(): any[] {
    return Array.isArray(this.artifactFlowReadModel?.groups?.by_worker) ? this.artifactFlowReadModel.groups.by_worker : [];
  }

  artifactFlowAssignmentGroups(): any[] {
    return Array.isArray(this.artifactFlowReadModel?.groups?.by_assignment) ? this.artifactFlowReadModel.groups.by_assignment : [];
  }

  itemArtifacts(item: any): any[] {
    const artifacts = [
      ...(Array.isArray(item?.sent_artifacts) ? item.sent_artifacts : []),
      ...(Array.isArray(item?.returned_artifacts) ? item.returned_artifacts : []),
      ...((Array.isArray(item?.worker_jobs) ? item.worker_jobs : []).flatMap((job: any) => [
        ...(Array.isArray(job?.sent_artifacts) ? job.sent_artifacts : []),
        ...(Array.isArray(job?.returned_artifacts) ? job.returned_artifacts : []),
      ])),
    ];
    return this.uniqueArtifacts(artifacts);
  }

  groupArtifacts(group: any): any[] {
    return this.uniqueArtifacts(Array.isArray(group?.artifacts) ? group.artifacts : []);
  }

  itemWorkspaceFiles(item: any): any[] {
    const workerJobs = Array.isArray(item?.worker_jobs) ? item.worker_jobs : [];
    const files = workerJobs.flatMap((job: any) => this.workspaceFilesFromRefs(job?.returned_refs, {
      worker_job_id: job?.worker_job_id,
      worker_url: job?.worker_url,
      worker_name: job?.worker_name,
    }));
    return this.uniqueWorkspaceFiles(files);
  }

  workerWorkspaceFiles(group: any): any[] {
    const workerUrl = String(group?.worker_url || '').trim();
    if (!workerUrl) return [];
    const files = this.artifactFlowItems().flatMap((item: any) => {
      const workerJobs = Array.isArray(item?.worker_jobs) ? item.worker_jobs : [];
      return workerJobs
        .filter((job: any) => String(job?.worker_url || '').trim() === workerUrl)
        .flatMap((job: any) => this.workspaceFilesFromRefs(job?.returned_refs, {
          worker_job_id: job?.worker_job_id,
          worker_url: job?.worker_url,
          worker_name: job?.worker_name,
        }));
    });
    return this.uniqueWorkspaceFiles(files);
  }

  artifactCount(item: any): number {
    return this.itemArtifacts(item).length;
  }

  assignmentLabel(group: any): string {
    return String(group?.template_name || group?.agent_name || group?.assignment_key || 'Unbekannte Zuordnung').trim();
  }

  workspaceCandidates(): any[] {
    const candidates: any[] = [];
    const seen = new Set<string>();
    for (const item of this.artifactFlowItems()) {
      const workerJobs = Array.isArray(item?.worker_jobs) ? item.worker_jobs : [];
      for (const job of workerJobs) {
        const workerUrl = String(job?.worker_url || '').trim();
        const subtaskId = String(job?.subtask_id || '').trim();
        if (!workerUrl || !subtaskId) continue;
        const key = `${workerUrl}::${subtaskId}`;
        if (seen.has(key)) continue;
        seen.add(key);
        candidates.push({
          key,
          worker_url: workerUrl,
          worker_name: String(job?.worker_name || '').trim() || workerUrl,
          task_id: subtaskId,
          worker_job_id: String(job?.worker_job_id || '').trim() || undefined,
          task_title: String(item?.title || item?.task_title || item?.task_id || '').trim(),
          updated_at: Number(job?.updated_at || item?.updated_at || 0),
        });
      }
    }
    candidates.sort((a, b) => (Number(b.updated_at || 0) - Number(a.updated_at || 0)));
    return candidates;
  }

  workspaceRunLabel(candidate: any): string {
    const worker = String(candidate?.worker_name || candidate?.worker_url || 'worker').trim();
    const taskId = String(candidate?.task_id || '').trim();
    const title = String(candidate?.task_title || '').trim();
    if (!title) return `${worker} · ${taskId}`;
    return `${worker} · ${taskId} · ${title}`;
  }

  workspaceSelectionChanged() {
    this.workspaceFilePayload = null;
    this.workspaceTreeLineItems = [];
    this.workspaceLoadError = '';
  }

  loadSelectedWorkspaceFiles() {
    const candidate = this.workspaceCandidates().find((item: any) => item.key === this.selectedWorkspaceRunKey);
    if (!candidate) return;
    this.workspaceLoading = true;
    this.workspaceLoadError = '';
    this.agentApi.taskWorkspaceFiles(
      candidate.worker_url,
      candidate.task_id,
      undefined,
      { trackedOnly: this.workspaceTrackedOnly, maxEntries: 4000 },
    ).pipe(
      finalize(() => {
        this.workspaceLoading = false;
      }),
    ).subscribe({
      next: (payload) => {
        this.workspaceFilePayload = payload;
        const files = Array.isArray(payload?.workspace?.files) ? payload.workspace.files : [];
        this.workspaceTreeLineItems = this.buildWorkspaceTreeLines(files);
      },
      error: (error) => {
        this.workspaceFilePayload = null;
        this.workspaceTreeLineItems = [];
        this.workspaceLoadError = this.ns.fromApiError(error, 'Workspace-Dateien konnten nicht geladen werden');
      },
    });
  }

  workspaceInspectorMeta(): any {
    const workspace = this.workspaceFilePayload?.workspace;
    return workspace && typeof workspace === 'object' ? workspace : null;
  }

  workspaceTreeLines(): any[] {
    return this.workspaceTreeLineItems;
  }

  selectArtifactBySummary(artifact: any) {
    const artifactId = String(artifact?.artifact_id || '').trim();
    if (!artifactId) return;
    this.selectArtifact(artifactId);
  }

  private uniqueArtifacts(artifacts: any[]): any[] {
    const seen = new Set<string>();
    return artifacts.filter((artifact) => {
      const artifactId = String(artifact?.artifact_id || '').trim();
      if (!artifactId || seen.has(artifactId)) return false;
      seen.add(artifactId);
      return true;
    });
  }

  private workspaceFilesFromRefs(refs: any, fallback: { worker_job_id?: string; worker_url?: string; worker_name?: string }): any[] {
    const rows = Array.isArray(refs) ? refs : [];
    const files = rows
      .filter((ref: any) => ref && typeof ref === 'object')
      .map((ref: any) => {
        const workspaceRelativePath = String(ref.workspace_relative_path || '').trim();
        if (!workspaceRelativePath) return null;
        return {
          kind: String(ref.kind || '').trim() || 'workspace_file',
          workspace_relative_path: workspaceRelativePath,
          artifact_id: String(ref.artifact_id || '').trim() || undefined,
          filename: String(ref.filename || '').trim() || undefined,
          worker_job_id: String(ref.worker_job_id || fallback.worker_job_id || '').trim() || undefined,
          worker_url: String(ref.worker_url || fallback.worker_url || '').trim() || undefined,
          worker_name: String(ref.worker_name || fallback.worker_name || '').trim() || undefined,
        };
      })
      .filter((entry: any) => !!entry);
    return files as any[];
  }

  private uniqueWorkspaceFiles(files: any[]): any[] {
    const seen = new Set<string>();
    return files.filter((file) => {
      const key = [
        String(file?.worker_url || '').trim(),
        String(file?.worker_job_id || '').trim(),
        String(file?.workspace_relative_path || '').trim(),
        String(file?.artifact_id || '').trim(),
      ].join('|');
      if (!key.replace(/\|/g, '').trim()) return false;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  private ensureWorkspaceSelection() {
    const candidates = this.workspaceCandidates();
    const hasCurrent = candidates.some((item: any) => item.key === this.selectedWorkspaceRunKey);
    if (hasCurrent) return;
    this.selectedWorkspaceRunKey = candidates[0]?.key || '';
    this.workspaceFilePayload = null;
    this.workspaceTreeLineItems = [];
    this.workspaceLoadError = '';
  }

  private buildWorkspaceTreeLines(files: any[]): any[] {
    const rows = Array.isArray(files) ? files : [];
    const normalized = rows
      .map((item: any) => ({
        relative_path: String(item?.relative_path || '').trim().replace(/\\/g, '/'),
        size_bytes: Number(item?.size_bytes || 0),
      }))
      .filter((item: any) => !!item.relative_path)
      .sort((a: any, b: any) => a.relative_path.localeCompare(b.relative_path));

    const lines: any[] = [];
    const seenDirs = new Set<string>();
    for (const file of normalized) {
      const parts = file.relative_path.split('/').filter((part: string) => !!part);
      if (!parts.length) continue;
      for (let index = 0; index < parts.length - 1; index += 1) {
        const dirPath = parts.slice(0, index + 1).join('/');
        if (seenDirs.has(dirPath)) continue;
        seenDirs.add(dirPath);
        lines.push({
          type: 'dir',
          path: dirPath,
          name: parts[index],
          depth: index,
        });
      }
      lines.push({
        type: 'file',
        path: file.relative_path,
        name: parts[parts.length - 1],
        depth: Math.max(0, parts.length - 1),
        size_bytes: Number.isFinite(file.size_bytes) ? file.size_bytes : 0,
      });
    }
    return lines;
  }
}
