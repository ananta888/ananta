import { Injectable, inject } from '@angular/core';
import { map, throwError, timeout } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { HubApiCoreService } from '../../services/hub-api-core.service';

export interface MeetBinding {
  schema: 'ananta.meet-binding.v1';
  project_id: string;
  task_id: string | null;
  revision: number;
  invite_url: string | null;
  room_verified: false;
  membership_granted: false;
  profile: { origin: string; create_url: string; creation_mode: 'meet_ui_then_attach' };
}

export interface MeetTurn {
  schema: 'ananta.meet-turn-result.v1';
  task_id: string;
  lease_id: string;
  text: string;
  audio: { mime: 'audio/wav'; base64: string };
  video: { mime: 'video/mp4'; base64: string };
  duration_seconds: number;
  meeting?: { status: 'published'; room_id: string; delivery_verified: false };
}

export function validateMeetBinding(value: MeetBinding, project: string, task: string): MeetBinding {
  if (!value || value.schema !== 'ananta.meet-binding.v1'
      || value.project_id !== project || value.task_id !== (task || null)
      || !Number.isSafeInteger(value.revision) || value.revision < 0
      || value.room_verified !== false || value.membership_granted !== false) {
    throw new Error('meet_contract_invalid');
  }
  const origin = new URL(value.profile.origin);
  if (origin.protocol !== 'https:' || origin.origin !== value.profile.origin
      || origin.username || origin.password || origin.port
      || value.profile.creation_mode !== 'meet_ui_then_attach'
      || value.profile.create_url !== `${origin.origin}/`) {
    throw new Error('meet_origin_invalid');
  }
  if (value.invite_url !== null) {
    const invite = new URL(value.invite_url);
    const room = invite.searchParams.get('room');
    if (!room || !/^room-[a-f0-9]{18}$/.test(room)
        || value.invite_url !== `${origin.origin}/?room=${room}&mode=room`) {
      throw new Error('meet_invite_invalid');
    }
  }
  return value;
}

@Injectable({ providedIn: 'root' })
export class MeetApiService {
  private readonly core = inject(HubApiCoreService);
  private readonly directory = inject(AgentDirectoryService);

  turn(project: string, text: string, publishToMeet = false, task = '') {
    const hub = this.directory.list().find(agent => agent.role === 'hub')?.url;
    if (!hub) return throwError(() => new Error('meet_hub_unavailable'));
    const taskPath = task ? `/tasks/${encodeURIComponent(task)}` : '';
    const url = `${hub.replace(/\/$/, '')}/api/meet/v1/projects/${encodeURIComponent(project)}${taskPath}/turns`;
    const body = publishToMeet ? { text, publish_to_meet: true } : { text };
    return this.core.request<MeetTurn>('POST', url, hub, { body }).pipe(timeout(120_000));
  }

  binding(project: string, task = '', method: 'GET' | 'PUT' | 'DELETE' = 'GET', body?: unknown) {
    const hub = this.directory.list().find(agent => agent.role === 'hub')?.url;
    if (!hub) return throwError(() => new Error('meet_hub_unavailable'));
    const root = `${hub.replace(/\/$/, '')}/api/meet/v1/projects/${encodeURIComponent(project)}`;
    const path = task ? `/tasks/${encodeURIComponent(task)}/binding` : '/binding';
    return this.core.request<MeetBinding>(method, root + path, hub, { body }).pipe(
      map(value => validateMeetBinding(value, project, task)),
    );
  }
}
