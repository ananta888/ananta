import { Injectable, inject } from '@angular/core';
import { map, throwError, timeout } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { HubApiCoreService } from '../../services/hub-api-core.service';
import { MeetAvatarSelection, validateAvatarSelection } from './meet-avatar-selection';
import { MeetAvatarVideoSelection, validateAvatarVideoSelection } from './meet-avatar-video-selection';
import { MeetVoiceSelection, validateVoiceSelection } from './meet-voice-selection';
import { DialogStartReceipt, dialogStartRequest } from './meet-dialog-start-request';
import { validateDialogPhase } from './meet-dialog-phase';
import { validateDialogDiagnostics } from './meet-dialog-diagnostics';
import { browserCommand, validateBrowserStatus } from './meet-browser-workspace';

export interface SourceControl { enabled: boolean; revision: number; since: number }
export type DialogSource = 'chat' | 'audio' | 'screen' | 'speech' | 'avatar' | 'visual';
export interface DialogControls {
  revision: number; chat: SourceControl; audio: SourceControl; screen: SourceControl;
  speech?: SourceControl; avatar?: SourceControl; visual?: SourceControl;
}
export const optionalDialogSources = ['speech', 'avatar', 'visual'] as const;
const optionalCapabilities = { speech: 'speech.publish', avatar: 'avatar.publish', visual: 'video.receive' } as const;
export interface MeetDialog {
  schema: 'ananta.meet-dialog-status.v1'; task_id: string; status: string; deadline: number;
  controls: DialogControls; capabilities: string[];
  avatar_selection?: MeetAvatarSelection | MeetAvatarVideoSelection;
  avatar_videos?: true;
  voice_selection?: MeetVoiceSelection;
  browser_workspace?: true;
}
const capabilities = ['chat.read', 'chat.send', 'audio.receive', 'screen.publish', 'avatar.publish', 'speech.publish', 'video.receive'];
export function validateDialog(value: MeetDialog): MeetDialog {
  if (!value || Object.keys(value).filter(key => !['avatar_selection', 'avatar_videos', 'voice_selection', 'browser_workspace'].includes(key)).sort().join() !== 'capabilities,controls,deadline,schema,status,task_id'
    || value.schema !== 'ananta.meet-dialog-status.v1' || typeof value.task_id !== 'string' || !/^[A-Za-z0-9_.:-]{1,160}$/.test(value.task_id)
    || !['in_progress', 'completed', 'failed', 'cancelled'].includes(value.status)
    || !Number.isSafeInteger(value.deadline) || value.deadline <= 0 || !Array.isArray(value.capabilities)
    || value.capabilities.some(v => !capabilities.includes(v)) || new Set(value.capabilities).size !== value.capabilities.length) {
    throw new Error('meet_dialog_contract_invalid');
  }
  if (Object.hasOwn(value, 'avatar_selection')) {
    if (!value.capabilities.includes('avatar.publish') || !value.controls?.avatar) throw new Error('meet_dialog_contract_invalid');
    if (value.avatar_selection?.mode === 'persona-video-v1') {
      if (value.avatar_videos !== true) throw new Error('meet_dialog_contract_invalid');
      validateAvatarVideoSelection(value.avatar_selection);
    } else validateAvatarSelection(value.avatar_selection!);
  }
  if (Object.hasOwn(value, 'avatar_videos') && (value.avatar_videos !== true || !value.avatar_selection)) throw new Error('meet_dialog_contract_invalid');
  if (Object.hasOwn(value, 'browser_workspace') && (value.browser_workspace !== true || !value.capabilities.includes('screen.publish'))) throw new Error('meet_dialog_contract_invalid');
  if (Object.hasOwn(value, 'voice_selection')) {
    if (!value.capabilities.includes('speech.publish') || !value.controls?.speech) throw new Error('meet_dialog_contract_invalid');
    validateVoiceSelection(value.voice_selection!);
  }
  const controls = value.controls;
  if (!controls || Object.keys(controls).filter(key => !(optionalDialogSources as readonly string[]).includes(key)).sort().join() !== 'audio,chat,revision,screen'
    || optionalDialogSources.some(name => Object.hasOwn(controls, name) && !value.capabilities.includes(optionalCapabilities[name]))
    || !Number.isSafeInteger(controls.revision) || controls.revision < 1 || controls.revision > 1023) throw new Error('meet_dialog_contract_invalid');
  const sources: DialogSource[] = ['chat', 'audio', 'screen', ...optionalDialogSources.filter(name => Object.hasOwn(controls, name))];
  for (const name of sources) {
    const source = controls[name];
    if (!source || Object.keys(source).sort().join() !== 'enabled,revision,since' || typeof source.enabled !== 'boolean'
      || !Number.isSafeInteger(source.revision) || source.revision < 1 || source.revision > controls.revision
      || !Number.isSafeInteger(source.since) || source.since < 1) throw new Error('meet_dialog_contract_invalid');
  }
  return value;
}

@Injectable({ providedIn: 'root' })
export class MeetDialogApiService {
  private readonly core = inject(HubApiCoreService);
  private readonly directory = inject(AgentDirectoryService);
  private request<T>(project: string, path: string, method: 'GET' | 'POST' | 'DELETE' | 'PATCH' | 'PUT', body?: unknown,
    headers?: Record<string, string>) {
    const hub = this.directory.list().find(agent => agent.role === 'hub')?.url;
    if (!hub) return throwError(() => new Error('meet_hub_unavailable'));
    const root = `${hub.replace(/\/$/, '')}/api/meet/v1/projects/${encodeURIComponent(project)}`;
    return this.core.request<T>(method, root + path, hub, { body, ...(headers ? { headers } : {}) }).pipe(timeout(10_000));
  }
  list(project: string, cursor = 0) {
    return this.request<{schema: string; items: MeetDialog[]; next_cursor: number | null}>(project, `/dialogs?cursor=${cursor}`, 'GET').pipe(map(value => {
      if (!value || Object.keys(value).sort().join() !== 'items,next_cursor,schema' || value.schema !== 'ananta.meet-dialog-list.v1'
        || !Array.isArray(value.items) || value.items.length > 50 || value.next_cursor !== null
        && (!Number.isSafeInteger(value.next_cursor) || value.next_cursor !== cursor + 50)) throw new Error('meet_dialog_contract_invalid');
      value.items.forEach(validateDialog); return value;
    }));
  }
  start(project: string, task: string, body: unknown) {
    let command: ReturnType<typeof dialogStartRequest>;
    try { command = dialogStartRequest(body); }
    catch { return throwError(() => new Error('meet_dialog_start_request_invalid')); }
    return this.request<DialogStartReceipt>(project,
      (task ? `/tasks/${encodeURIComponent(task)}` : '') + '/dialogs', 'POST', command.body, command.headers).pipe(map(value => {
      if (!value || Object.keys(value).sort().join() !== 'schema,session_id,status,task_id'
        || value.schema !== 'ananta.meet-dialog-start.v1' || value.status !== 'connecting'
        || ![value.task_id, value.session_id].every(id => typeof id === 'string' && /^[A-Za-z0-9_.:-]{1,160}$/.test(id))) throw new Error('meet_dialog_contract_invalid');
      return value;
    }));
  }
  stop(project: string, task: string) { return this.request<MeetDialog>(project, `/dialogs/${encodeURIComponent(task)}`, 'DELETE').pipe(map(validateDialog)); }
  phase(project: string, task: string, refresh = false) {
    return this.request<unknown>(project, `/dialogs/${encodeURIComponent(task)}/phase`, refresh ? 'POST' : 'GET')
      .pipe(map(value => validateDialogPhase(value, task)));
  }
  diagnostics(project: string, task: string) {
    return this.request<unknown>(project, `/dialogs/${encodeURIComponent(task)}/diagnostics`, 'GET')
      .pipe(map(value => validateDialogDiagnostics(value, task)));
  }
  control(project: string, task: string, body: unknown) {
    return this.request<MeetDialog>(project, `/dialogs/${encodeURIComponent(task)}`, 'PATCH', body).pipe(map(validateDialog));
  }
  selectAvatar(project: string, task: string, body: unknown) {
    return this.request<MeetDialog>(project, `/dialogs/${encodeURIComponent(task)}/avatar`, 'PUT', body).pipe(map(validateDialog));
  }
  selectVoice(project: string, task: string, body: unknown) {
    return this.request<MeetDialog>(project, `/dialogs/${encodeURIComponent(task)}/voice`, 'PUT', body).pipe(map(validateDialog));
  }
  selectAvatarVideo(project: string, task: string, body: unknown) {
    return this.request<MeetDialog>(project, `/dialogs/${encodeURIComponent(task)}/avatar-video`, 'PUT', body).pipe(map(validateDialog));
  }
  browserStatus(project: string, task: string) {
    return this.request<unknown>(project, `/dialogs/${encodeURIComponent(task)}/browser`, 'GET').pipe(map(value => validateBrowserStatus(value, task)));
  }
  browserCommand(project: string, task: string, body: unknown) {
    let command: ReturnType<typeof browserCommand>;
    try { command = browserCommand(body); }
    catch { return throwError(() => new Error('meet_browser_control_invalid')); }
    return this.request<unknown>(project, `/dialogs/${encodeURIComponent(task)}/browser`, 'POST', command).pipe(map(value => validateBrowserStatus(value, task)));
  }
}
