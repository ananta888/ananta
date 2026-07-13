import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { UserAuthService } from './user-auth.service';

export const WORKFLOW_STREAM_REQUEST_SCHEMA = 'ananta.workflow_stream_request.v1';
export const WORKFLOW_STREAM_FRAME_SCHEMA = 'ananta.workflow_stream_frame.v1';

export interface WorkflowRuntimeStreamRequest {
  schema: typeof WORKFLOW_STREAM_REQUEST_SCHEMA;
  workflow_id: string;
  after_cursor?: string;
  max_events?: number;
  wait_seconds?: number;
  heartbeat_seconds?: number;
}

export interface WorkflowRuntimeStreamFrame {
  schema: typeof WORKFLOW_STREAM_FRAME_SCHEMA;
  event_type: string;
  workflow_id: string;
  run_id: string;
  step_id: string;
  cursor: string;
  event_id: string;
  occurred_at: number;
  payload: Record<string, unknown>;
}

export interface WorkflowRuntimeStreamPage {
  frames: WorkflowRuntimeStreamFrame[];
  nextCursor: string;
  hasMore: boolean;
}

export interface WorkflowRuntimeStreamOptions {
  token?: string;
  afterCursor?: string;
  maxEvents?: number;
  heartbeatSeconds?: number;
}

@Injectable({ providedIn: 'root' })
export class WorkflowRuntimeStreamService {
  private auth = inject(UserAuthService);

  async readPage(
    hubUrl: string,
    workflowId: string,
    options: WorkflowRuntimeStreamOptions = {},
    signal?: AbortSignal,
  ): Promise<WorkflowRuntimeStreamPage> {
    const token = options.token || this.auth.token;
    if (!token) throw new Error('workflow_stream_auth_required');
    const request: WorkflowRuntimeStreamRequest = {
      schema: WORKFLOW_STREAM_REQUEST_SCHEMA,
      workflow_id: workflowId,
      after_cursor: options.afterCursor || '',
      max_events: options.maxEvents ?? 128,
      heartbeat_seconds: options.heartbeatSeconds ?? 15,
    };
    const response = await fetch(`${hubUrl.replace(/\/$/, '')}/api/visual-process/workflow/events/stream`, {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      signal,
      headers: {
        Accept: 'application/x-ndjson',
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(request),
    });
    if (!response.ok) throw new Error(`workflow_stream_http_${response.status}`);
    const frames = this.parseFrames(await response.text(), workflowId);
    const nextCursor = response.headers.get('X-Workflow-Next-Cursor') || options.afterCursor || '';
    if (this.cursorOffset(nextCursor) < this.cursorOffset(options.afterCursor || '')) {
      throw new Error('workflow_stream_cursor_regressed');
    }
    return {
      frames,
      nextCursor,
      hasMore: response.headers.get('X-Workflow-Has-More') === 'true',
    };
  }

  async cancel(
    hubUrl: string,
    workflowId: string,
    reason = 'client_cancelled',
    token = this.auth.token,
    signal?: AbortSignal,
  ): Promise<void> {
    if (!token) throw new Error('workflow_stream_auth_required');
    if (reason.length > 1000) throw new Error('workflow_cancel_reason_too_long');
    const response = await fetch(
      `${hubUrl.replace(/\/$/, '')}/api/visual-process/workflow/${encodeURIComponent(workflowId)}/cancel`,
      {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        signal,
        headers: {
          Accept: 'application/json',
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ reason }),
      },
    );
    if (!response.ok) throw new Error(`workflow_cancel_http_${response.status}`);
  }

  connect(
    hubUrl: string,
    workflowId: string,
    options: WorkflowRuntimeStreamOptions = {},
  ): Observable<WorkflowRuntimeStreamFrame> {
    return new Observable(observer => {
      const abort = new AbortController();
      let cursor = options.afterCursor || '';
      let timer: ReturnType<typeof setTimeout> | undefined;
      let closed = false;
      const seenEventIds = new Set<string>();

      const poll = async (): Promise<void> => {
        if (closed) return;
        try {
          const page = await this.readPage(
            hubUrl,
            workflowId,
            { ...options, afterCursor: cursor },
            abort.signal,
          );
          if (closed) return;
          cursor = page.nextCursor;
          for (const frame of page.frames) {
            if (seenEventIds.has(frame.event_id)) continue;
            seenEventIds.add(frame.event_id);
            if (seenEventIds.size > 8192) {
              throw new Error('workflow_stream_dedupe_window_exceeded');
            }
            observer.next(frame);
          }
          const delay = page.hasMore ? 0 : Math.max(1, options.heartbeatSeconds ?? 15) * 1000;
          timer = setTimeout(() => void poll(), delay);
        } catch (error) {
          if (!closed && !abort.signal.aborted) observer.error(error);
        }
      };

      void poll();
      return () => {
        closed = true;
        if (timer) clearTimeout(timer);
        abort.abort();
      };
    });
  }

  private parseFrames(body: string, workflowId: string): WorkflowRuntimeStreamFrame[] {
    return body
      .split('\n')
      .map(line => line.trim())
      .filter(Boolean)
      .map(line => {
        const raw = JSON.parse(line) as Partial<WorkflowRuntimeStreamFrame>;
        if (
          raw.schema !== WORKFLOW_STREAM_FRAME_SCHEMA
          || raw.workflow_id !== workflowId
          || typeof raw.event_type !== 'string'
          || typeof raw.cursor !== 'string'
          || typeof raw.event_id !== 'string'
          || typeof raw.payload !== 'object'
          || raw.payload === null
        ) {
          throw new Error('workflow_stream_frame_invalid');
        }
        return raw as WorkflowRuntimeStreamFrame;
      });
  }

  private cursorOffset(cursor: string): number {
    if (!cursor) return 0;
    const match = /^v1:(\d+)$/.exec(cursor);
    if (!match) throw new Error('workflow_stream_cursor_invalid');
    return Number(match[1]);
  }
}
