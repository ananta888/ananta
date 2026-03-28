export function normalizeTaskStatus(status) {
    const raw = String(status || '').trim().toLowerCase();
    if (!raw)
        return 'todo';
    const map = {
        'to-do': 'todo',
        'backlog': 'todo',
        'in-progress': 'in_progress',
        'done': 'completed',
        'complete': 'completed'
    };
    return (map[raw] || raw).replace(/[- ]/g, '_');
}
export function isTaskDone(status) {
    return normalizeTaskStatus(status) === 'completed';
}
export function isTaskInProgress(status) {
    return normalizeTaskStatus(status) === 'in_progress';
}
export function taskStatusDisplayLabel(status) {
    const normalized = normalizeTaskStatus(status);
    const labels = {
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
//# sourceMappingURL=task-status.js.map