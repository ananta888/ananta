import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders, HttpParams } from '@angular/common/http';
import { Observable, map } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import {
  ApiEnvelope,
  ConflictDecisionCommand,
  CuratedWikiPage,
  CursorPage,
  KnowledgeConflict,
  KnowledgeConflictDetail,
  KnowledgeHealthSnapshot,
  KnowledgeCorrectionDetail,
} from './knowledge-hygiene.models';

@Injectable({ providedIn: 'root' })
export class KnowledgeHygieneApiService {
  private readonly http = inject(HttpClient);
  private readonly directory = inject(AgentDirectoryService);

  private get base(): string {
    const hub = this.directory.list().find(agent => agent.role === 'hub');
    return (hub?.url ?? 'http://127.0.0.1:5000') + '/api/knowledge-hygiene';
  }

  health(projectId: string): Observable<KnowledgeHealthSnapshot> {
    return this.get<KnowledgeHealthSnapshot>(projectId, '/health');
  }

  conflicts(
    projectId: string,
    state?: string,
    cursor?: string
  ): Observable<CursorPage<KnowledgeConflict>> {
    let params = new HttpParams().set('limit', '100');
    if (state) params = params.set('state', state);
    if (cursor) params = params.set('cursor', cursor);
    return this.http.get<ApiEnvelope<CursorPage<KnowledgeConflict>>>(
      this.projectUrl(projectId, '/conflicts'),
      { params, headers: this.projectHeaders(projectId) }
    ).pipe(map(response => response.data));
  }

  conflict(projectId: string, conflictId: string): Observable<KnowledgeConflictDetail> {
    return this.get<KnowledgeConflictDetail>(
      projectId,
      '/conflicts/' + encodeURIComponent(conflictId)
    );
  }

  wiki(projectId: string, cursor?: string): Observable<CursorPage<CuratedWikiPage>> {
    let params = new HttpParams().set('limit', '100');
    if (cursor) params = params.set('cursor', cursor);
    return this.http.get<ApiEnvelope<CursorPage<CuratedWikiPage>>>(
      this.projectUrl(projectId, '/wiki'),
      { params, headers: this.projectHeaders(projectId) }
    ).pipe(map(response => response.data));
  }

  wikiPage(projectId: string, slug: string, revision?: number): Observable<CuratedWikiPage> {
    let params = new HttpParams();
    if (revision) params = params.set('revision', String(revision));
    return this.http.get<ApiEnvelope<CuratedWikiPage>>(
      this.projectUrl(projectId, '/wiki/' + encodeURIComponent(slug)),
      { params, headers: this.projectHeaders(projectId) }
    ).pipe(map(response => response.data));
  }

  decide(
    projectId: string,
    conflictId: string,
    command: ConflictDecisionCommand
  ): Observable<KnowledgeConflict> {
    return this.http.post<ApiEnvelope<KnowledgeConflict>>(
      this.projectUrl(projectId, '/conflicts/' + encodeURIComponent(conflictId) + '/decisions'),
      command,
      { headers: this.commandHeaders(projectId, command.decision_id) }
    ).pipe(map(response => response.data));
  }

  correction(
    projectId: string,
    correctionId: string
  ): Observable<KnowledgeCorrectionDetail> {
    return this.get<KnowledgeCorrectionDetail>(
      projectId,
      '/corrections/' + encodeURIComponent(correctionId)
    );
  }

  approveWriteback(
    projectId: string,
    correctionId: string,
    proposalDigest: string
  ): Observable<Record<string, string>> {
    const approvalId = crypto.randomUUID();
    return this.http.post<ApiEnvelope<Record<string, string>>>(
      this.projectUrl(projectId, '/corrections/' + encodeURIComponent(correctionId) + '/writeback'),
      { approval_id: approvalId, proposal_digest: proposalDigest },
      { headers: this.commandHeaders(projectId, approvalId) }
    ).pipe(map(response => response.data));
  }

  private get<T>(projectId: string, suffix: string): Observable<T> {
    return this.http.get<ApiEnvelope<T>>(
      this.projectUrl(projectId, suffix),
      { headers: this.projectHeaders(projectId) }
    ).pipe(map(response => response.data));
  }

  private projectUrl(projectId: string, suffix: string): string {
    return this.base + '/projects/' + encodeURIComponent(projectId) + suffix;
  }

  private projectHeaders(projectId: string): HttpHeaders {
    return new HttpHeaders({ 'X-Ananta-Project-Id': projectId });
  }

  private commandHeaders(projectId: string, idempotencyKey: string): HttpHeaders {
    return this.projectHeaders(projectId).set('Idempotency-Key', idempotencyKey);
  }
}
