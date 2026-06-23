import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';

export type PanelId = 'A' | 'B' | 'C';
export type AiMode = 'review' | 'explain' | 'risk' | 'tests' | 'patch' | 'chat';
export type LayoutMode = 'equal' | 'left-wide' | 'right-wide' | 'focus' | 'compact' | 'focus-a' | 'focus-b' | 'focus-c';
export type SourceKind = 'current_diff' | 'output_artifact' | 'ai' | 'empty' | 'file_content';

export interface DiffSourceRef {
  schema: string;
  source_ref_id: string;
  source_kind: string;
  display_name: string;
  locator: Record<string, unknown>;
  content_hash?: string;
}

export interface DiffPanel {
  panel_id: PanelId;
  panel_type: string;
  source_left: DiffSourceRef | null;
  source_right: DiffSourceRef | null;
  render_mode: string;
  filters: Record<string, string>;
  scroll_state: { line: number };
  selection_state: Record<string, unknown>;
}

export interface AiPanelState {
  schema: string;
  mode: AiMode;
  selected_panels: PanelId[];
  selected_hunks: string[];
  context_refs: string[];
  prompt_template_ref: string;
  status: 'idle' | 'running' | 'completed' | 'degraded';
  last_response_ref: string;
  updated_at: string;
}

export interface AiDiffResponse {
  schema: string;
  status: string;
  artifact_type: string;
  summary: string;
  findings: string[];
  risks: string[];
  suggested_tests: string[];
  patch_suggestions: string[];
  source_refs: string[];
  reason_code?: string;
}

export interface Diff3Session {
  schema: string;
  session_id: string;
  goal_id?: string;
  panels: DiffPanel[];
  active_panel: PanelId;
  layout_mode: LayoutMode;
  created_at: string;
  updated_at: string;
  extensions: {
    ai_panel_state?: AiPanelState;
    ai_last_response?: AiDiffResponse;
    ai_last_findings?: string[];
    sync_scroll?: boolean;
    [key: string]: unknown;
  };
}

export interface PanelContent {
  ok: boolean;
  content_type?: string;
  patch?: string;
  text?: string;
  left_text?: string;
  right_text?: string;
  left_ref?: string;
  right_ref?: string;
  reason_code?: string;
  source_kind?: string;
  display_name?: string;
}

export interface AiRunResult {
  session: Diff3Session;
  ai_result: {
    status: string;
    response: AiDiffResponse;
    context_envelope: Record<string, unknown>;
    provenance_id: string;
    output_artifact_id: string;
  };
}

@Injectable({ providedIn: 'root' })
export class Diff3ApiService {
  private http = inject(HttpClient);
  private dir  = inject(AgentDirectoryService);

  private get base(): string {
    return this.dir.list().find(a => a.role === 'hub')?.url ?? '';
  }

  createSession(goalId?: string, layoutMode: LayoutMode = 'equal'): Observable<Diff3Session> {
    return this.http.post<Diff3Session>(`${this.base}/api/diff3/sessions`, {
      goal_id: goalId ?? null,
      layout_mode: layoutMode,
    });
  }

  getSession(sessionId: string): Observable<Diff3Session> {
    return this.http.get<Diff3Session>(`${this.base}/api/diff3/sessions/${sessionId}`);
  }

  deleteSession(sessionId: string): Observable<{ ok: boolean }> {
    return this.http.delete<{ ok: boolean }>(`${this.base}/api/diff3/sessions/${sessionId}`);
  }

  updatePanel(sessionId: string, panelId: PanelId, body: {
    source_kind: SourceKind;
    render_mode?: string;
    output_artifact_id?: string;
    goal_id?: string;
    ai_mode?: AiMode;
    path_filter?: string;
    path?: string;
  }): Observable<Diff3Session> {
    return this.http.put<Diff3Session>(
      `${this.base}/api/diff3/sessions/${sessionId}/panels/${panelId}`, body
    );
  }

  setFocus(sessionId: string, panelId: PanelId): Observable<Diff3Session> {
    return this.http.put<Diff3Session>(
      `${this.base}/api/diff3/sessions/${sessionId}/focus`, { panel_id: panelId }
    );
  }

  setLayout(sessionId: string, layoutMode: LayoutMode): Observable<Diff3Session> {
    return this.http.put<Diff3Session>(
      `${this.base}/api/diff3/sessions/${sessionId}/layout`, { layout_mode: layoutMode }
    );
  }

  setSync(sessionId: string, sync: boolean): Observable<Diff3Session> {
    return this.http.put<Diff3Session>(
      `${this.base}/api/diff3/sessions/${sessionId}/sync`, { sync }
    );
  }

  runAi(sessionId: string, mode: AiMode, goalId?: string): Observable<AiRunResult> {
    return this.http.post<AiRunResult>(
      `${this.base}/api/diff3/sessions/${sessionId}/ai/run`,
      { mode, goal_id: goalId ?? null }
    );
  }

  setAiMode(sessionId: string, mode: AiMode): Observable<Diff3Session> {
    return this.http.put<Diff3Session>(
      `${this.base}/api/diff3/sessions/${sessionId}/ai/mode`, { mode }
    );
  }

  getPanelContent(sessionId: string, panelId: PanelId): Observable<PanelContent> {
    return this.http.get<PanelContent>(
      `${this.base}/api/diff3/sessions/${sessionId}/panels/${panelId}/content`
    );
  }
}
