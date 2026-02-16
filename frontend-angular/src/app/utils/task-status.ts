export function normalizeTaskStatus(status: string | undefined | null): string {
  const raw = String(status || '').trim().toLowerCase();
  if (!raw) return 'todo';
  const map: Record<string, string> = {
    'to-do': 'todo',
    'backlog': 'todo',
    'in-progress': 'in_progress',
    'done': 'completed',
    'complete': 'completed'
  };
  return (map[raw] || raw).replace(/[- ]/g, '_');
}

export function isTaskDone(status: string | undefined | null): boolean {
  return normalizeTaskStatus(status) === 'completed';
}

export function isTaskInProgress(status: string | undefined | null): boolean {
  return normalizeTaskStatus(status) === 'in_progress';
}

export function taskStatusDisplayLabel(status: string | undefined | null): string {
  const normalized = normalizeTaskStatus(status);
  const labels: Record<string, string> = {
    todo: 'To Do',
    in_progress: 'In Progress',
    blocked: 'Blocked',
    completed: 'Completed',
    failed: 'Failed',
    assigned: 'Assigned',
    created: 'Created',
    proposing: 'Proposing'
  };
  return labels[normalized] || normalized.replace(/_/g, ' ');
}
