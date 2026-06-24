import { Component, OnInit, inject } from '@angular/core';
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
  styleUrls: ['./artifacts.component.css'],
  templateUrl: './artifacts.component.html',
})
export class ArtifactsComponent implements OnInit {
  private dir     = inject(AgentDirectoryService);
  private hubApi  = inject(AdminFacade);
  private ns      = inject(NotificationService);
  private agentApi = inject(AgentApiService);

  hub = this.dir.list().find((a) => a.role === 'hub');

  // ── artifact list ────────────────────────────────────────────────────────────
  artifacts: any[] = [];
  selectedArtifactId: string | null = null;
  selectedArtifact: any = null;
  loadingList   = false;
  loadingDetail = false;
  extractBusy   = false;
  indexBusy     = false;
  previewBusy   = false;
  uploadBusy    = false;
  artifactRagStatus:  any = null;
  artifactRagPreview: any = null;
  selectedFile: File | null = null;
  collectionName = '';
  knowledgeCollections: any[] = [];
  selectedCollectionId: string | null = null;
  loadingCollections = false;
  collectionIndexBusy = false;

  // ── profiles ─────────────────────────────────────────────────────────────────
  knowledgeProfiles: any[] = [];
  selectedArtifactProfileName = 'default';

  // ── artifact flow ────────────────────────────────────────────────────────────
  artifactFlowReadModel: any = null;
  loadingArtifactFlow = false;

  // ── workspace inspector ──────────────────────────────────────────────────────
  selectedWorkspaceRunKey = '';
  workspaceTrackedOnly    = true;
  workspaceLoading        = false;
  workspaceLoadError      = '';
  workspaceFilePayload:   any = null;
  workspaceTreeLineItems: any[] = [];

  term                = userFacingTerm;
  decisionExplanation = decisionExplanation;

  ngOnInit(): void {
    this.refresh();
    this.loadProfiles();
    this.loadCollections();
    this.loadArtifactFlow();
  }

  // ── profiles ─────────────────────────────────────────────────────────────────
  private loadProfiles(): void {
    if (!this.hub) return;
    this.hubApi.listKnowledgeIndexProfiles(this.hub.url).subscribe({
      next: (payload) => {
        const items = Array.isArray(payload?.items) ? payload.items : [];
        this.knowledgeProfiles = items;
        const def = items.find((i: any) => i?.is_default)?.name || items[0]?.name || 'default';
        if (!this.selectedArtifactProfileName || this.selectedArtifactProfileName === 'default') {
          this.selectedArtifactProfileName = def;
        }
      },
      error: () => { this.knowledgeProfiles = []; },
    });
  }

  // ── artifact list ────────────────────────────────────────────────────────────
  refresh(): void {
    if (!this.hub) return;
    this.loadingList = true;
    this.hubApi.listArtifacts(this.hub.url).pipe(
      finalize(() => { this.loadingList = false; }),
    ).subscribe({
      next: (items) => {
        this.artifacts = Array.isArray(items) ? items : [];
        if (!this.selectedArtifactId && this.artifacts.length) {
          this.selectArtifact(this.artifacts[0].id);
          return;
        }
        if (this.selectedArtifactId) {
          const stillExists = this.artifacts.some((a) => a.id === this.selectedArtifactId);
          if (stillExists) {
            this.selectArtifact(this.selectedArtifactId);
          } else {
            this.selectedArtifactId = null;
            this.selectedArtifact   = null;
          }
        }
      },
      error: (e) => this.ns.error(this.ns.fromApiError(e, 'Artefakte konnten nicht geladen werden')),
    });
    this.loadArtifactFlow();
  }

  loadCollections(): void {
    if (!this.hub) return;
    this.loadingCollections = true;
    this.hubApi.listKnowledgeCollections(this.hub.url).pipe(
      finalize(() => { this.loadingCollections = false; }),
    ).subscribe({
      next: (items) => {
        this.knowledgeCollections = Array.isArray(items) ? items : [];
        if (!this.selectedCollectionId && this.knowledgeCollections.length) {
          this.selectedCollectionId = String(this.knowledgeCollections[0]?.id || '') || null;
        }
      },
      error: () => { this.knowledgeCollections = []; this.selectedCollectionId = null; },
    });
  }

  onFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement | null;
    this.selectedFile = input?.files?.[0] || null;
  }

  knowledgeCollectionNames(artifact: any): string[] {
    const links = Array.isArray(artifact?.knowledge_links) ? artifact.knowledge_links : [];
    const names: string[] = [];
    const seen = new Set<string>();
    for (const link of links) {
      const name = String(link?.link_metadata?.collection_name || link?.collection_name || '').trim();
      if (!name || seen.has(name)) continue;
      seen.add(name);
      names.push(name);
    }
    return names;
  }

  upload(): void {
    if (!this.hub || !this.selectedFile) return;
    const file = this.selectedFile;
    const collectionName = this.collectionName.trim() || undefined;
    this.uploadBusy = true;
    this.hubApi.uploadArtifact(this.hub.url, file, collectionName).pipe(
      finalize(() => { this.uploadBusy = false; }),
    ).subscribe({
      next: (payload) => {
        this.ns.success('Artefakt hochgeladen');
        this.refresh();
        this.loadCollections();
        const artifactId = String(payload?.artifact?.id || '').trim();
        if (artifactId) {
          this.selectArtifact(artifactId);
        }
      },
      error: (e) => this.ns.error(this.ns.fromApiError(e, 'Artefakt-Upload fehlgeschlagen')),
    });
  }

  indexSelectedCollection(): void {
    if (!this.hub || !this.selectedCollectionId) return;
    this.collectionIndexBusy = true;
    this.hubApi.indexKnowledgeCollection(this.hub.url, this.selectedCollectionId, {
      profile_name: this.selectedArtifactProfileName || 'default',
    }).pipe(
      finalize(() => { this.collectionIndexBusy = false; }),
    ).subscribe({
      next: () => this.ns.success('Collection indexiert'),
      error: (e) => this.ns.error(this.ns.fromApiError(e, 'Collection-Index fehlgeschlagen')),
    });
  }

  selectArtifact(artifactId: string): void {
    if (!this.hub || !artifactId) return;
    this.selectedArtifactId = artifactId;
    this.loadingDetail = true;
    this.hubApi.getArtifact(this.hub.url, artifactId).pipe(
      finalize(() => { this.loadingDetail = false; }),
    ).subscribe({
      next: (p) => {
        this.selectedArtifact   = p;
        this.artifactRagStatus  = null;
        this.artifactRagPreview = null;
        this.loadSelectedRagDetails();
      },
      error: (e) => this.ns.error(this.ns.fromApiError(e, 'Artifact-Details konnten nicht geladen werden')),
    });
  }

  extractSelected(): void {
    if (!this.hub || !this.selectedArtifactId) return;
    this.extractBusy = true;
    this.hubApi.extractArtifact(this.hub.url, this.selectedArtifactId).pipe(
      finalize(() => { this.extractBusy = false; }),
    ).subscribe({
      next: () => { this.ns.success('Extraktion gestartet'); this.selectArtifact(this.selectedArtifactId!); this.refresh(); },
      error: (e) => this.ns.error(this.ns.fromApiError(e, 'Extraktion fehlgeschlagen')),
    });
  }

  indexSelected(): void {
    if (!this.hub || !this.selectedArtifactId) return;
    this.indexBusy = true;
    this.hubApi.indexArtifact(this.hub.url, this.selectedArtifactId, {
      profile_name: this.selectedArtifactProfileName || 'default',
    }).pipe(finalize(() => { this.indexBusy = false; })).subscribe({
      next: () => { this.ns.success('RAG-Index erstellt'); this.selectArtifact(this.selectedArtifactId!); },
      error: (e) => this.ns.error(this.ns.fromApiError(e, 'RAG-Index fehlgeschlagen')),
    });
  }

  loadSelectedRagDetails(): void {
    if (!this.hub || !this.selectedArtifactId) return;
    this.previewBusy = true;
    this.hubApi.getArtifactRagStatus(this.hub.url, this.selectedArtifactId).subscribe({
      next: (p) => { this.artifactRagStatus = p; },
      error: () => { this.artifactRagStatus = null; },
    });
    this.hubApi.getArtifactRagPreview(this.hub.url, this.selectedArtifactId, 5).pipe(
      finalize(() => { this.previewBusy = false; }),
    ).subscribe({
      next: (p) => { this.artifactRagPreview = p; },
      error: () => { this.artifactRagPreview = null; },
    });
  }

  selectedArtifactSummaryMetrics(): SummaryMetric[] {
    return [
      { label: 'Versionen',             value: this.selectedArtifact?.versions?.length || 0 },
      { label: 'Extrahierte Dokumente', value: this.selectedArtifact?.extracted_documents?.length || 0 },
      { label: 'Wissenslinks',          value: this.selectedArtifact?.knowledge_links?.length || 0 },
      { label: 'RAG-Status',            value: this.selectedArtifact?.knowledge_index?.status || this.artifactRagStatus?.knowledge_index?.status || 'nicht indexiert' },
      { label: 'Index-Profil',          value: this.selectedArtifact?.knowledge_index?.profile_name || this.artifactRagStatus?.knowledge_index?.profile_name || 'default' },
    ];
  }

  // ── artifact flow ────────────────────────────────────────────────────────────
  loadArtifactFlow(): void {
    if (!this.hub) return;
    this.loadingArtifactFlow = true;
    this.hubApi.getTaskOrchestrationReadModel(this.hub.url).pipe(
      finalize(() => { this.loadingArtifactFlow = false; }),
    ).subscribe({
      next: (p) => { this.artifactFlowReadModel = p?.artifact_flow || null; this.ensureWorkspaceSelection(); },
      error: () => { this.artifactFlowReadModel = null; this.selectedWorkspaceRunKey = ''; this.workspaceFilePayload = null; this.workspaceTreeLineItems = []; },
    });
  }

  artifactFlowItems(): any[]            { return Array.isArray(this.artifactFlowReadModel?.items)                      ? this.artifactFlowReadModel.items                     : []; }
  artifactFlowWorkerGroups(): any[]     { return Array.isArray(this.artifactFlowReadModel?.groups?.by_worker)           ? this.artifactFlowReadModel.groups.by_worker           : []; }
  artifactFlowAssignmentGroups(): any[] { return Array.isArray(this.artifactFlowReadModel?.groups?.by_assignment)       ? this.artifactFlowReadModel.groups.by_assignment       : []; }

  itemArtifacts(item: any): any[] {
    const arts = [
      ...(Array.isArray(item?.sent_artifacts)     ? item.sent_artifacts     : []),
      ...(Array.isArray(item?.returned_artifacts) ? item.returned_artifacts : []),
      ...((Array.isArray(item?.worker_jobs) ? item.worker_jobs : []).flatMap((j: any) => [
        ...(Array.isArray(j?.sent_artifacts)     ? j.sent_artifacts     : []),
        ...(Array.isArray(j?.returned_artifacts) ? j.returned_artifacts : []),
      ])),
    ];
    return this.uniqueArtifacts(arts);
  }

  groupArtifacts(group: any): any[] { return this.uniqueArtifacts(Array.isArray(group?.artifacts) ? group.artifacts : []); }

  itemWorkspaceFiles(item: any): any[] {
    const jobs = Array.isArray(item?.worker_jobs) ? item.worker_jobs : [];
    const files = jobs.flatMap((j: any) => this.workspaceFilesFromRefs(j?.returned_refs, { worker_job_id: j?.worker_job_id, worker_url: j?.worker_url, worker_name: j?.worker_name }));
    return this.uniqueWorkspaceFiles(files);
  }

  workerWorkspaceFiles(group: any): any[] {
    const workerUrl = String(group?.worker_url || '').trim();
    if (!workerUrl) return [];
    const files = this.artifactFlowItems().flatMap((item: any) =>
      (Array.isArray(item?.worker_jobs) ? item.worker_jobs : [])
        .filter((j: any) => String(j?.worker_url || '').trim() === workerUrl)
        .flatMap((j: any) => this.workspaceFilesFromRefs(j?.returned_refs, { worker_job_id: j?.worker_job_id, worker_url: j?.worker_url, worker_name: j?.worker_name }))
    );
    return this.uniqueWorkspaceFiles(files);
  }

  artifactCount(item: any): number { return this.itemArtifacts(item).length; }
  assignmentLabel(group: any): string { return String(group?.template_name || group?.agent_name || group?.assignment_key || 'Unbekannte Zuordnung').trim(); }

  selectArtifactBySummary(artifact: any): void {
    const id = String(artifact?.artifact_id || '').trim();
    if (id) this.selectArtifact(id);
  }

  // ── workspace inspector ──────────────────────────────────────────────────────
  workspaceCandidates(): any[] {
    const candidates: any[] = [];
    const seen = new Set<string>();
    for (const item of this.artifactFlowItems()) {
      for (const job of (Array.isArray(item?.worker_jobs) ? item.worker_jobs : [])) {
        const workerUrl = String(job?.worker_url || '').trim();
        const subtaskId = String(job?.subtask_id  || '').trim();
        if (!workerUrl || !subtaskId) continue;
        const key = `${workerUrl}::${subtaskId}`;
        if (seen.has(key)) continue;
        seen.add(key);
        candidates.push({
          key,
          worker_url:  workerUrl,
          worker_name: String(job?.worker_name || '').trim() || workerUrl,
          task_id:     subtaskId,
          worker_job_id: String(job?.worker_job_id || '').trim() || undefined,
          task_title:  String(item?.title || item?.task_title || item?.task_id || '').trim(),
          updated_at:  Number(job?.updated_at || item?.updated_at || 0),
        });
      }
    }
    return candidates.sort((a, b) => Number(b.updated_at) - Number(a.updated_at));
  }

  workspaceRunLabel(c: any): string {
    const worker = String(c?.worker_name || c?.worker_url || 'worker').trim();
    const taskId = String(c?.task_id || '').trim();
    const title  = String(c?.task_title || '').trim();
    return title ? `${worker} · ${taskId} · ${title}` : `${worker} · ${taskId}`;
  }

  workspaceSelectionChanged(): void {
    this.workspaceFilePayload   = null;
    this.workspaceTreeLineItems = [];
    this.workspaceLoadError     = '';
  }

  loadSelectedWorkspaceFiles(): void {
    const candidate = this.workspaceCandidates().find((c: any) => c.key === this.selectedWorkspaceRunKey);
    if (!candidate) return;
    this.workspaceLoading   = true;
    this.workspaceLoadError = '';
    this.agentApi.taskWorkspaceFiles(candidate.worker_url, candidate.task_id, undefined, { trackedOnly: this.workspaceTrackedOnly, maxEntries: 4000 }).pipe(
      finalize(() => { this.workspaceLoading = false; }),
    ).subscribe({
      next: (p) => {
        this.workspaceFilePayload   = p;
        this.workspaceTreeLineItems = this.buildWorkspaceTreeLines(Array.isArray(p?.workspace?.files) ? p.workspace.files : []);
      },
      error: (e) => { this.workspaceFilePayload = null; this.workspaceTreeLineItems = []; this.workspaceLoadError = this.ns.fromApiError(e, 'Workspace-Dateien konnten nicht geladen werden'); },
    });
  }

  workspaceInspectorMeta(): any { const w = this.workspaceFilePayload?.workspace; return w && typeof w === 'object' ? w : null; }
  workspaceTreeLines(): any[]   { return this.workspaceTreeLineItems; }

  private ensureWorkspaceSelection(): void {
    const candidates = this.workspaceCandidates();
    if (candidates.some((c: any) => c.key === this.selectedWorkspaceRunKey)) return;
    this.selectedWorkspaceRunKey = candidates[0]?.key || '';
    this.workspaceFilePayload    = null;
    this.workspaceTreeLineItems  = [];
    this.workspaceLoadError      = '';
  }

  private uniqueArtifacts(artifacts: any[]): any[] {
    const seen = new Set<string>();
    return artifacts.filter((a) => {
      const id = String(a?.artifact_id || '').trim();
      if (!id || seen.has(id)) return false;
      seen.add(id);
      return true;
    });
  }

  private workspaceFilesFromRefs(refs: any, fallback: { worker_job_id?: string; worker_url?: string; worker_name?: string }): any[] {
    return (Array.isArray(refs) ? refs : [])
      .filter((r: any) => r && typeof r === 'object')
      .map((r: any) => {
        const path = String(r.workspace_relative_path || '').trim();
        if (!path) return null;
        return {
          kind:                    String(r.kind || '').trim() || 'workspace_file',
          workspace_relative_path: path,
          artifact_id:             String(r.artifact_id  || '').trim() || undefined,
          filename:                String(r.filename      || '').trim() || undefined,
          worker_job_id:           String(r.worker_job_id || fallback.worker_job_id || '').trim() || undefined,
          worker_url:              String(r.worker_url    || fallback.worker_url    || '').trim() || undefined,
          worker_name:             String(r.worker_name   || fallback.worker_name   || '').trim() || undefined,
        };
      })
      .filter((e: any) => !!e) as any[];
  }

  private uniqueWorkspaceFiles(files: any[]): any[] {
    const seen = new Set<string>();
    return files.filter((f) => {
      const key = [String(f?.worker_url || ''), String(f?.worker_job_id || ''), String(f?.workspace_relative_path || ''), String(f?.artifact_id || '')].join('|');
      if (!key.replace(/\|/g, '').trim() || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  private buildWorkspaceTreeLines(files: any[]): any[] {
    const normalized = (Array.isArray(files) ? files : [])
      .map((f: any) => ({ relative_path: String(f?.relative_path || '').trim().replace(/\\/g, '/'), size_bytes: Number(f?.size_bytes || 0) }))
      .filter((f: any) => !!f.relative_path)
      .sort((a: any, b: any) => a.relative_path.localeCompare(b.relative_path));

    const lines: any[] = [];
    const seenDirs = new Set<string>();
    for (const file of normalized) {
      const parts = file.relative_path.split('/').filter((p: string) => !!p);
      if (!parts.length) continue;
      for (let i = 0; i < parts.length - 1; i++) {
        const dir = parts.slice(0, i + 1).join('/');
        if (!seenDirs.has(dir)) { seenDirs.add(dir); lines.push({ type: 'dir',  path: dir,                name: parts[i],              depth: i }); }
      }
      lines.push({ type: 'file', path: file.relative_path, name: parts[parts.length - 1], depth: Math.max(0, parts.length - 1), size_bytes: Number.isFinite(file.size_bytes) ? file.size_bytes : 0 });
    }
    return lines;
  }
}
