import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, Observable } from 'rxjs';
import { map, tap } from 'rxjs/operators';
import { HubApiCoreService } from './hub-api-core.service';
import { AgentDirectoryService } from './agent-directory.service';

export interface ChatSession {
  id: string;
  name: string;
  icon: string;
  group: string;
  folder_id: string;
  session_type: string;
  session_subtype: string;
  type_description: string;
  last_message_preview: string;
  message_count: number;
  system_prompt: string;
  settings: ChatSettingsMap;
  settings_delta: ChatSettingsMap;
  profile_id: string;
  process_ref?: ChatProcessRef | null;
  system_prompt_override?: string;
  created_at?: number;
  updated_at?: number;
  sort_order?: number;
}

export interface ChatProfile {
  id: string;
  name: string;
  icon: string;
  description?: string;
  system_prompt: string;
  settings: ChatSettingsMap;
  builtin: boolean;
  process_ref?: ChatProcessRef | null;
}
export type ChatSettingValue = string|number|boolean|null;
export type ChatSettingsMap = Record<string,ChatSettingValue>;

export interface ChatProcessRef { graph_id: string; version: string; }
export interface EffectiveChatProcess {
  process_ref: ChatProcessRef | null;
  source: EffectiveChatProcessSource;
  graph: Record<string, unknown> | null;
  run: Record<string, unknown> | null;
}
export type EffectiveChatProcessSource = 'session_override' | 'profile' | 'global';

/** Compatibility boundary for responses written before the shared source enum. */
export function normalizeEffectiveChatProcessSource(value: unknown): EffectiveChatProcessSource {
  if (value === 'session' || value === 'session_override') return 'session_override';
  if (value === 'profile') return 'profile';
  return 'global';
}

function normalizeEffectiveChatProcess(value: EffectiveChatProcess): EffectiveChatProcess {
  return { ...value, source: normalizeEffectiveChatProcessSource(value?.source) };
}
export interface ChatProcessRunSummary { run_id:string; workflow_id:string; process_id:string; process_version:string; snapshot_hash:string; status:string; message_id?:string; started_at:number; }

export interface ChatSettingDefinition {
  key: string;
  label: string;
  group: string;
  type: 'boolean' | 'integer' | 'number' | 'string' | 'enum';
  default: unknown;
  scope_defaults: Record<string, unknown>;
  allowed_values: unknown[];
  suggestions?: string[];
  constraints?: { min?: number; max?: number; step?: number };
  visible_when?: Record<string, unknown[]>;
  scopes: string[];
  secret: boolean;
  advanced: boolean;
  deprecated: boolean;
}

export interface ChatSettingSchema {
  schema_version: number;
  settings: ChatSettingDefinition[];
}

export interface ChatSessionType {
  id: string;
  name: string;
  icon: string;
  description: string;
  subtypes: string[];
  builtin: boolean;
}

export interface ChatFolder {
  id: string;
  name: string;
  icon: string;
  parent_id: string;
  color?: string;
  created_at?: number;
  updated_at?: number;
  sort_order?: number;
}

export interface OrganizationOperation {
  operation_id: string;
  type: string;
  target_id?: string;
  temp_id?: string;
  before?: unknown;
  after?: unknown;
  rationale?: string;
}

export interface ReorganizeProposal {
  id: string;
  status: 'draft' | 'ready' | 'invalid' | 'applied' | 'discarded' | 'superseded';
  base_state_hash: string;
  input_policy: 'metadata_only' | 'metadata_plus_preview';
  operations: OrganizationOperation[];
  validation_errors: Array<{ operation_id?: string; error_code: string; message: string }>;
  folders: ChatFolder[];
  assignments: Record<string, string>;
  summary: string;
  method?: 'llm' | 'heuristic';
}

export interface OrganizationSnapshot {
  folders: ChatFolder[];
  conversations: Array<Pick<ChatSession, 'id' | 'name' | 'folder_id' | 'profile_id' | 'session_type' | 'session_subtype'>>;
  state_hash: string;
}

export interface OrganizationRevision {
  id: string;
  created_at: number;
  source: 'user' | 'ai';
  source_proposal_id?: string;
  reverts_revision_id?: string;
  summary: string;
  applied_operations: OrganizationOperation[];
  base_state_hash: string;
  result_state_hash: string;
  before_snapshot: OrganizationSnapshot;
  after_snapshot: OrganizationSnapshot;
}

export interface PartialSummaryResult {
  summary: string;
  method: 'llm' | 'extractive';
  source_count: number;
  chars: number;
}

export interface PromptPreviewSection {
  name: string;
  enabled: boolean;
  chars: number;
  truncated: boolean;
  text: string;
}

export interface PromptPreview {
  session_id: string;
  sections: PromptPreviewSection[];
  total_chars: number;
  assembled_prompt: string;
}

export interface ContextOverview {
  session_id: string;
  system_prompt: { text: string; chars: number; enabled: boolean };
  history: { enabled: boolean; max_turns: number; max_chars: number };
  summary: { enabled: boolean; max_chars: number };
  rag: { enabled: boolean; profile: string; top_k: number; max_chars: number };
}

export interface CreateSessionPayload {
  id?: string;
  name: string;
  icon?: string;
  group?: string;
  folder_id?: string;
  session_type?: string;
  session_subtype?: string;
  type_description?: string;
  system_prompt?: string;
  settings?: ChatSettingsMap;
  profile_id?: string;
}

@Injectable({ providedIn: 'root' })
export class ChatSessionsService {
  private core = inject(HubApiCoreService);
  private dir = inject(AgentDirectoryService);

  readonly sessions$ = new BehaviorSubject<ChatSession[]>([]);
  readonly folders$ = new BehaviorSubject<ChatFolder[]>([]);
  readonly profiles$ = new BehaviorSubject<ChatProfile[]>([]);
  readonly settingSchema$ = new BehaviorSubject<ChatSettingSchema>({ schema_version: 1, settings: [] });
  readonly types$ = new BehaviorSubject<ChatSessionType[]>([]);
  readonly activeSessionId$ = new BehaviorSubject<string>('');
  readonly loading$ = new BehaviorSubject<boolean>(false);
  readonly error$ = new BehaviorSubject<string>('');

  private get hubUrl(): string {
    return this.dir.list().find(a => a.role === 'hub')?.url ?? '';
  }

  load(): void {
    const url = this.hubUrl;
    if (!url) return;
    this.loading$.next(true);
    this.core.get<ChatSession[]>(`${url}/api/chat/sessions`, url).subscribe({
      next: sessions => {
        const list = Array.isArray(sessions) ? sessions : [];
        this.sessions$.next(list);
        if (!this.activeSessionId$.value && list.length) {
          this.activeSessionId$.next(list[0].id);
        }
        this.loading$.next(false);
        this.error$.next('');
      },
      error: err => {
        this.error$.next(String(err?.message || 'Fehler beim Laden der Sessions'));
        this.loading$.next(false);
      },
    });
    this.loadFolders();
    this.loadProfiles();
    this.loadSettingSchema();
    this.loadTypes();
  }

  loadTypes(): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.get<ChatSessionType[]>(`${url}/api/chat/types`, url).subscribe({
      next: types => this.types$.next(Array.isArray(types) ? types : []),
      error: err => this.error$.next(String(err?.message || 'Fehler beim Laden der Chat-Typen')),
    });
  }

  createType(type: Partial<ChatSessionType> & { name: string }): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.post<ChatSessionType>(`${url}/api/chat/types`, type, url).subscribe({
      next: () => this.loadTypes(),
      error: err => this.error$.next(String(err?.message || 'Fehler beim Erstellen des Chat-Typs')),
    });
  }

  updateType(typeId: string, patch: Partial<ChatSessionType>): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.patch<ChatSessionType>(`${url}/api/chat/types/${typeId}`, patch, url).subscribe({
      next: () => this.loadTypes(),
      error: err => this.error$.next(String(err?.message || 'Chat-Typ konnte nicht aktualisiert werden')),
    });
  }

  deleteType(typeId: string): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.delete<void>(`${url}/api/chat/types/${typeId}`, url).subscribe({
      next: () => this.loadTypes(),
      error: err => this.error$.next(String(err?.message || 'Chat-Typ wird noch verwendet oder konnte nicht gelöscht werden')),
    });
  }

  loadProfiles(): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.get<ChatProfile[]>(`${url}/api/chat/profiles`, url).subscribe({
      next: profiles => this.profiles$.next(Array.isArray(profiles) ? profiles : []),
      error: err => this.error$.next(String(err?.message || 'Fehler beim Laden der Chat-Profile')),
    });
  }

  loadSettingSchema(): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.get<ChatSettingSchema>(`${url}/api/chat/settings/schema`, url).subscribe({
      next: schema => this.settingSchema$.next(schema),
      error: err => this.error$.next(String(err?.message || 'Einstellungskatalog konnte nicht geladen werden')),
    });
  }

  createProfile(profile: Partial<ChatProfile> & { name: string }): Observable<ChatProfile> {
    const url = this.hubUrl;
    return this.core.post<ChatProfile>(`${url}/api/chat/profiles`, profile, url).pipe(tap(() => this.loadProfiles()));
  }

  updateProfile(profileId: string, patch: Partial<ChatProfile>): Observable<ChatProfile> {
    const url = this.hubUrl;
    return this.core.patch<ChatProfile>(`${url}/api/chat/profiles/${profileId}`, patch, url).pipe(tap(() => { this.loadProfiles(); this.load(); }));
  }

  discoverProfileModels(draft: Record<string, unknown>): Observable<{ ok: boolean; models: string[]; error_code?: string }> {
    const url = this.hubUrl;
    return this.core.post<{ ok: boolean; models: string[]; error_code?: string }>(`${url}/api/chat/profiles/models`, draft, url);
  }

  testProfileConnection(draft: Record<string, unknown>): Observable<{ ok: boolean; model_status: string; error_code?: string }> {
    const url = this.hubUrl;
    return this.core.post<{ ok: boolean; model_status: string; error_code?: string }>(`${url}/api/chat/profiles/test-connection`, draft, url);
  }
  previewProfile(profileId:string,profileSettings:ChatSettingsMap,sessionDelta:ChatSettingsMap={}):Observable<Record<string,unknown>>{const url=this.hubUrl;return this.core.post<Record<string,unknown>>(`${url}/api/chat/profiles/effective-preview`,{profile_id:profileId||'general',profile_settings:profileSettings,session_settings_delta:sessionDelta},url);}

  deleteProfile(profileId: string): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.delete<void>(`${url}/api/chat/profiles/${profileId}`, url).subscribe({
      next: () => this.loadProfiles(),
      error: err => this.error$.next(String(err?.message || 'Profil wird noch verwendet oder konnte nicht gelöscht werden')),
    });
  }

  getEffectiveProcess(sessionId: string): Observable<EffectiveChatProcess> {
    const url = this.hubUrl;
    return this.core.get<EffectiveChatProcess>(`${url}/api/chat/sessions/${sessionId}/process`, url).pipe(
      map(normalizeEffectiveChatProcess),
    );
  }

  cloneEffectiveProcess(sessionId: string): Observable<EffectiveChatProcess> {
    const url = this.hubUrl;
    return this.core.post<EffectiveChatProcess>(`${url}/api/chat/sessions/${sessionId}/process/clone`, {}, url).pipe(
      map(normalizeEffectiveChatProcess),
      tap(() => this.load()),
    );
  }
  listProcessRuns(sessionId:string): Observable<ChatProcessRunSummary[]> { const url=this.hubUrl; return this.core.get<ChatProcessRunSummary[]>(`${url}/api/chat/sessions/${sessionId}/process/runs`,url); }
  startProcessRun(sessionId:string,messageId=''): Observable<ChatProcessRunSummary> { const url=this.hubUrl; return this.core.post<ChatProcessRunSummary>(`${url}/api/chat/sessions/${sessionId}/process/runs`,{message_id:messageId},url); }
  getProcessRun(sessionId:string,runId:string): Observable<Record<string,unknown>> { const url=this.hubUrl; return this.core.get<Record<string,unknown>>(`${url}/api/chat/sessions/${sessionId}/process/runs/${runId}`,url); }
  signalProcessGate(sessionId:string,runId:string,stepId:string,decision:'approve'|'reject'): Observable<Record<string,unknown>> { const url=this.hubUrl; const idempotency_key=globalThis.crypto?.randomUUID?.()||`${runId}-${stepId}-${decision}`;return this.core.post<Record<string,unknown>>(`${url}/api/chat/sessions/${sessionId}/process/runs/${runId}/gate`,{step_id:stepId,decision,idempotency_key},url); }

  loadFolders(): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.get<ChatFolder[]>(`${url}/api/chat/folders`, url).subscribe({
      next: folders => {
        this.folders$.next(Array.isArray(folders) ? folders : []);
      },
      error: () => { /* silently ignore until endpoint is available */ },
    });
  }

  createFolder(name: string, icon?: string, parentId?: string): Observable<ChatFolder> {
    const url = this.hubUrl;
    const body: Partial<ChatFolder> = { name, icon: icon || '📁' };
    if (parentId) body.parent_id = parentId;
    return this.core.post<ChatFolder>(`${url}/api/chat/folders`, body, url).pipe(
      tap(() => this.loadFolders()),
    );
  }

  updateFolder(id: string, patch: Partial<ChatFolder>): Observable<ChatFolder> {
    const url = this.hubUrl;
    return this.core.patch<ChatFolder>(`${url}/api/chat/folders/${id}`, patch, url).pipe(
      tap(() => this.loadFolders()),
    );
  }

  deleteFolder(id: string): Observable<void> {
    const url = this.hubUrl;
    return this.core.delete<void>(`${url}/api/chat/folders/${id}`, url).pipe(
      tap(() => this.loadFolders()),
    );
  }

  aiReorganize(inputPolicy: 'metadata_only' | 'metadata_plus_preview' = 'metadata_only'): Observable<ReorganizeProposal> {
    const url = this.hubUrl;
    return this.core.post<ReorganizeProposal>(
      `${url}/api/chat/sessions/ai-reorganize`, { input_policy: inputPolicy }, url,
    );
  }

  updateProposal(id: string, patch: Partial<Pick<ReorganizeProposal, 'operations' | 'summary'>>): Observable<ReorganizeProposal> {
    const url = this.hubUrl;
    return this.core.patch<ReorganizeProposal>(`${url}/api/chat/organization/proposals/${id}`, patch, url);
  }

  validateProposal(id: string): Observable<ReorganizeProposal> {
    const url = this.hubUrl;
    return this.core.post<ReorganizeProposal>(`${url}/api/chat/organization/proposals/${id}/validate`, {}, url);
  }

  applyProposal(id: string): Observable<OrganizationRevision> {
    const url = this.hubUrl;
    return this.core.post<OrganizationRevision>(`${url}/api/chat/organization/proposals/${id}/apply`, {}, url).pipe(
      tap(() => this.load()),
    );
  }

  discardProposal(id: string): Observable<void> {
    const url = this.hubUrl;
    return this.core.delete<void>(`${url}/api/chat/organization/proposals/${id}`, url);
  }

  loadOrganizationHistory(): Observable<OrganizationRevision[]> {
    const url = this.hubUrl;
    return this.core.get<OrganizationRevision[]>(`${url}/api/chat/organization/history`, url);
  }

  revertRevision(id: string): Observable<OrganizationRevision> {
    const url = this.hubUrl;
    return this.core.post<OrganizationRevision>(`${url}/api/chat/organization/history/${id}/revert`, {}, url).pipe(
      tap(() => this.load()),
    );
  }

  summarizeMessages(
    sessionId: string,
    messages: { sender: string; text: string }[],
    targetChars?: number,
    instruction?: string,
  ): Observable<PartialSummaryResult> {
    const url = this.hubUrl;
    const body: Record<string, unknown> = { messages };
    if (targetChars != null) body['target_chars'] = targetChars;
    if (instruction) body['instruction'] = instruction;
    return this.core.post<PartialSummaryResult>(
      `${url}/api/chat/sessions/${sessionId}/summarize`, body, url,
    );
  }

  getPromptPreview(
    sessionId: string,
    message: string,
    history: { sender: string; text: string }[],
    summary?: string,
  ): Observable<PromptPreview> {
    const url = this.hubUrl;
    const body: Record<string, unknown> = { message, history };
    if (summary) body['summary'] = summary;
    return this.core.post<PromptPreview>(
      `${url}/api/chat/sessions/${sessionId}/prompt-preview`, body, url,
    );
  }

  getContextOverview(sessionId: string): Observable<ContextOverview> {
    const url = this.hubUrl;
    return this.core.get<ContextOverview>(`${url}/api/chat/sessions/${sessionId}/context-overview`, url);
  }

  activate(sessionId: string): void {
    const url = this.hubUrl;
    if (!url) return;
    this.activeSessionId$.next(sessionId);
    this.core.post<{ message: string }>(`${url}/api/chat/sessions/${sessionId}/activate`, {}, url).subscribe({
      next: () => this.load(),
      error: err => this.error$.next(String(err?.message || 'Fehler beim Aktivieren')),
    });
  }

  create(payload: CreateSessionPayload): void {
    const url = this.hubUrl;
    if (!url) return;
    const body = {
      id: payload.id || `session-${Date.now()}`,
      name: payload.name,
      icon: payload.icon || '💬',
      group: payload.group || '',
      folder_id: payload.folder_id || '',
      session_type: payload.session_type || '',
      session_subtype: payload.session_subtype || '',
      type_description: payload.type_description || '',
      system_prompt: payload.system_prompt || '',
      settings: payload.settings || {},
      profile_id: payload.profile_id || 'general',
    };
    this.core.post<ChatSession>(`${url}/api/chat/sessions`, body, url).subscribe({
      next: s => {
        this.load();
        this.activeSessionId$.next(s.id);
      },
      error: err => this.error$.next(String(err?.message || 'Fehler beim Erstellen')),
    });
  }

  update(sessionId: string, patch: Partial<Pick<ChatSession,
    'name' | 'icon' | 'group' | 'folder_id' | 'session_type' | 'session_subtype' | 'type_description' | 'system_prompt' | 'settings' | 'profile_id'
  >>): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.patch<ChatSession>(`${url}/api/chat/sessions/${sessionId}`, patch, url).subscribe({
      next: () => this.load(),
      error: err => this.error$.next(String(err?.message || 'Fehler beim Aktualisieren')),
    });
  }

  updateProcessRef(sessionId:string,processRef:ChatProcessRef|null):Observable<ChatSession>{const url=this.hubUrl;return this.core.patch<ChatSession>(`${url}/api/chat/sessions/${sessionId}`,{process_ref:processRef},url).pipe(tap(()=>this.load()));}

  /** Patch a single session setting optimistically (no full reload; background sync). */
  patchSetting(sessionId: string, key: string, value: ChatSettingValue): void {
    const updated = this.sessions$.value.map(s => {
      if (s.id !== sessionId) return s;
      return { ...s, settings: { ...(s.settings || {}), [key]: value } };
    });
    this.sessions$.next(updated);
    const url = this.hubUrl;
    if (!url) return;
    this.core.patch<ChatSession>(
      `${url}/api/chat/sessions/${sessionId}`,
      { settings: { [key]: value } },
      url,
    ).subscribe({
      error: err => this.error$.next(String(err?.message || 'Fehler beim Aktualisieren')),
    });
  }

  remove(sessionId: string): void {
    const url = this.hubUrl;
    if (!url) return;
    this.core.delete<void>(`${url}/api/chat/sessions/${sessionId}`, url).subscribe({
      next: () => {
        if (this.activeSessionId$.value === sessionId) {
          this.activeSessionId$.next('');
        }
        this.load();
      },
      error: err => this.error$.next(String(err?.message || 'Fehler beim Löschen')),
    });
  }
}
