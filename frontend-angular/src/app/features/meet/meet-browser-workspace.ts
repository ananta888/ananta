/** Selection/Task metadata only; never a capture, navigation or publication grant. */
export interface MeetBrowserStatus {
  schema: 'ananta.meet-browser-status.v1'; revision: number; mode: 'status' | 'browser' | 'off';
  dialog_task_id: string;
  task_id: string | null; deadline_ms: number | null;
  task_status: 'in_progress' | 'completed' | 'failed' | 'cancelled' | 'unavailable' | null;
}
export type BrowserCommand = { action: 'navigate'; expected_revision: number; url: string } |
  { action: 'present' | 'stop' | 'status'; expected_revision: number };

export function validateBrowserStatus(value: unknown, task: string): MeetBrowserStatus {
  if (!value || typeof value !== 'object') throw new Error('meet_browser_status_invalid');
  const item = value as MeetBrowserStatus;
  if (Object.keys(item).sort().join() !== 'deadline_ms,dialog_task_id,mode,revision,schema,task_id,task_status'
    || typeof item.dialog_task_id !== 'string' || !/^[A-Za-z0-9_.:-]{1,160}$/.test(item.dialog_task_id) || item.dialog_task_id !== task
    || item.schema !== 'ananta.meet-browser-status.v1' || !Number.isInteger(item.revision) || item.revision < 1 || item.revision > 1023
    || !['status', 'browser', 'off'].includes(item.mode)) throw new Error('meet_browser_status_invalid');
  if (item.task_id === null) {
    if (item.deadline_ms !== null || item.task_status !== null || item.mode === 'browser') throw new Error('meet_browser_status_invalid');
  } else if (typeof item.task_id !== 'string' || !/^[A-Za-z0-9_.:-]{1,160}$/.test(item.task_id)
    || item.mode === 'status' || !Number.isSafeInteger(item.deadline_ms) || item.deadline_ms! < 1
    || !['in_progress', 'completed', 'failed', 'cancelled', 'unavailable'].includes(item.task_status!)) {
    throw new Error('meet_browser_status_invalid');
  }
  return structuredClone(item);
}

export function browserCommand(value: unknown): BrowserCommand {
  if (!value || typeof value !== 'object') throw new Error('meet_browser_control_invalid');
  const item = value as BrowserCommand;
  const fields = item.action === 'navigate' ? 'action,expected_revision,url' : 'action,expected_revision';
  if (Object.keys(item).sort().join() !== fields || !['navigate', 'present', 'stop', 'status'].includes(item.action)
    || !Number.isInteger(item.expected_revision) || item.expected_revision < 1 || item.expected_revision >= 1023) {
    throw new Error('meet_browser_control_invalid');
  }
  if (item.action === 'navigate' && (typeof item.url !== 'string' || item.url.length > 2048
    || !/^https?:\/\//.test(item.url) || Array.from(item.url).some(char => char.charCodeAt(0) <= 32 || char.charCodeAt(0) === 127 || char === '\\'))) throw new Error('meet_browser_control_invalid');
  return { ...item }; // The Hub, not this UI preflight, owns complete target/policy admission.
}
