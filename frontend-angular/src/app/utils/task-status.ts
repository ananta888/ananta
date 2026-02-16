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
