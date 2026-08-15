/**
 * Who is busy with what, and what is coming into being.
 *
 * Both questions are answered from the tasks the Hub already holds. A task
 * carries its own status, the team it was scoped to and the agent it went to;
 * the timeline carries one event per thing that happened to it. Nothing is
 * inferred beyond grouping those two by the level a person is looking at.
 *
 * The buckets below are the whole idea: a person watching a team wants to
 * know what is moving, what is appearing, and what is stuck — in that order,
 * and without reading a status vocabulary of twenty words.
 */

export type WorkBucket = 'working' | 'emerging' | 'waiting' | 'finished';

/** Levels this view can be scoped to, coarsest first. */
export type WorkScopeLevel = 'organization' | 'unit' | 'team' | 'role_slot' | 'agent';

/**
 * Which slice of the work a level is asking about.
 *
 * A task summary carries an organisation, a unit, a team, a role slot and an
 * agent, so every level of the structure has an identity it can be asked by.
 * Only the agent is filtered by the Hub itself; the rest are narrowed on the
 * identity each task already carries.
 */
export interface WorkScope {
  readonly level: WorkScopeLevel;
  readonly organization_id?: string;
  readonly unit_id?: string;
  readonly team_id?: string;
  readonly role_slot_id?: string;
  /** Set for an agent scope; the Hub filters tasks by the agent's URL. */
  readonly agent?: string;
}

export interface TaskView {
  readonly id: string;
  readonly title: string;
  readonly status: string;
  readonly bucket: WorkBucket;
  readonly organization_id: string;
  readonly unit_id: string;
  readonly team_id: string;
  readonly role_slot_id: string;
  readonly agent: string;
  readonly kind: string;
  readonly updated_at: number;
  readonly created_at: number;
}

export interface AssigneeWork {
  readonly agent: string;
  readonly working: readonly TaskView[];
  readonly other: readonly TaskView[];
}

export interface TraceEntry {
  readonly key: string;
  readonly event_type: string;
  readonly actor: string;
  readonly task_id: string;
  readonly task_status: string;
  readonly summary: string;
  readonly occurred_at: number;
  readonly creating: boolean;
}

const WORKING = new Set(['in_progress', 'assigned', 'delegated']);
const EMERGING = new Set(['created', 'proposing', 'todo', 'reserved', 'updated']);
const WAITING = new Set(['waiting_for_review', 'blocked_by_dependency', 'blocked', 'paused']);

const BUCKET_LABELS: Readonly<Record<WorkBucket, string>> = {
  working: 'Arbeitet gerade',
  emerging: 'Entsteht gerade',
  waiting: 'Wartet',
  finished: 'Abgeschlossen',
};

const UNASSIGNED = 'ohne Zuweisung';
const MAX_TITLE = 160;
const MAX_TRACE = 300;

export function bucketLabel(bucket: WorkBucket): string {
  return BUCKET_LABELS[bucket];
}

/**
 * Which bucket a status belongs in.
 *
 * An unknown status counts as finished rather than as working: claiming an
 * agent is busy when the Hub used a word we do not know would be the one
 * mistake this view must not make.
 */
export function bucketOf(status: string): WorkBucket {
  const value = (status ?? '').trim().toLowerCase();
  if (WORKING.has(value)) return 'working';
  if (EMERGING.has(value)) return 'emerging';
  if (WAITING.has(value)) return 'waiting';
  return 'finished';
}

export function toTaskViews(rows: unknown): readonly TaskView[] {
  const list = Array.isArray(rows) ? rows : (rows as { tasks?: unknown })?.tasks;
  if (!Array.isArray(list)) return [];
  const views: TaskView[] = [];
  for (const row of list) {
    if (!row || typeof row !== 'object') continue;
    const record = row as Record<string, unknown>;
    const id = text(record['id']);
    if (!id) continue;
    const status = text(record['status']);
    views.push({
      id,
      title: text(record['title']).slice(0, MAX_TITLE) || text(record['description']).slice(0, MAX_TITLE) || id,
      status,
      bucket: bucketOf(status),
      organization_id: text(record['organization_id']),
      unit_id: text(record['unit_id']),
      team_id: text(record['team_id']),
      role_slot_id: text(record['role_slot_id']),
      agent: text(record['assigned_agent_url']),
      kind: text(record['task_kind']),
      updated_at: numeric(record['updated_at']),
      created_at: numeric(record['created_at']),
    });
  }
  return views;
}

export function inBucket(tasks: readonly TaskView[], bucket: WorkBucket): readonly TaskView[] {
  // Newest first: what changed last is what a person is looking for.
  return tasks.filter(task => task.bucket === bucket).slice().sort((left, right) => right.updated_at - left.updated_at);
}

export function bucketCounts(tasks: readonly TaskView[]): Readonly<Record<WorkBucket, number>> {
  const counts: Record<WorkBucket, number> = { working: 0, emerging: 0, waiting: 0, finished: 0 };
  for (const task of tasks) counts[task.bucket] += 1;
  return counts;
}

/**
 * Who is busy, one row per agent.
 *
 * Agents with something in hand come first, and tasks nobody was assigned are
 * kept under their own heading rather than hidden — an unassigned task is a
 * fact about the team, not noise.
 */
export function byAssignee(tasks: readonly TaskView[]): readonly AssigneeWork[] {
  const grouped = new Map<string, TaskView[]>();
  for (const task of tasks) {
    if (task.bucket === 'finished') continue;
    const key = task.agent || UNASSIGNED;
    grouped.set(key, [...(grouped.get(key) ?? []), task]);
  }
  const rows: AssigneeWork[] = [];
  for (const [agent, list] of grouped) {
    rows.push({
      agent,
      working: list.filter(task => task.bucket === 'working'),
      other: list.filter(task => task.bucket !== 'working'),
    });
  }
  return rows.sort((left, right) => {
    if (left.working.length !== right.working.length) return right.working.length - left.working.length;
    return left.agent.localeCompare(right.agent);
  });
}

/**
 * The timeline, as lines a person can read.
 *
 * The Hub's order is kept; only the shape changes. An event that brought a
 * task into existence is marked, because "what is being created right now" is
 * one of the two questions this whole view exists to answer.
 */
export function toTraceEntries(payload: unknown): readonly TraceEntry[] {
  const list = Array.isArray(payload) ? payload : timelineItems(payload);
  if (!Array.isArray(list)) return [];
  const entries: TraceEntry[] = [];
  for (const [index, item] of list.entries()) {
    if (!item || typeof item !== 'object') continue;
    const record = item as Record<string, unknown>;
    const eventType = text(record['event_type']) || 'task_activity';
    const taskId = text(record['task_id']);
    entries.push({
      key: `${taskId || 'event'}:${index}`,
      event_type: eventType,
      actor: text(record['actor']) || 'System',
      task_id: taskId,
      task_status: text(record['task_status']),
      summary: summarize(record),
      occurred_at: numeric(record['timestamp']),
      creating: eventType === 'task_created',
    });
  }
  return entries;
}

/**
 * Query parameters that scope the Hub's task reads to one level.
 *
 * Only what the Hub itself filters on goes here; everything else is narrowed
 * after the read. A parameter from the wrong level is never sent — that is
 * how one level ends up showing another's work.
 */
export function scopeQuery(scope: Readonly<WorkScope>): Readonly<Record<string, string>> {
  const query: Record<string, string> = {};
  if (scope.level === 'agent' && scope.agent) query['agent'] = scope.agent;
  if (scope.level === 'team' && scope.team_id) query['team_id'] = scope.team_id;
  return query;
}

/** The task field a level narrows on, and the value it must equal. */
export function scopeFilter(scope: Readonly<WorkScope>): { field: keyof TaskView; value: string } | null {
  switch (scope.level) {
    case 'unit':
      return scope.unit_id ? { field: 'unit_id', value: scope.unit_id } : null;
    case 'team':
      return scope.team_id ? { field: 'team_id', value: scope.team_id } : null;
    case 'role_slot':
      return scope.role_slot_id ? { field: 'role_slot_id', value: scope.role_slot_id } : null;
    case 'organization':
      return scope.organization_id ? { field: 'organization_id', value: scope.organization_id } : null;
    default:
      return null;
  }
}

export function narrowToScope(
  tasks: readonly TaskView[],
  scope: Readonly<WorkScope>,
): readonly TaskView[] {
  const filter = scopeFilter(scope);
  return filter ? tasks.filter(task => task[filter.field] === filter.value) : tasks;
}

/**
 * Whether a scope can actually be answered.
 *
 * A team with no identity to filter by would silently return the whole
 * organisation's work, which reads as "this team is doing everything".
 */
export function scopeIsAnswerable(scope: Readonly<WorkScope>): boolean {
  switch (scope.level) {
    case 'team':
      return Boolean(scope.team_id);
    case 'unit':
      return Boolean(scope.unit_id);
    case 'role_slot':
      return Boolean(scope.role_slot_id);
    case 'agent':
      return Boolean(scope.agent);
    default:
      // The organisation as a whole needs no identity: it is everything.
      return true;
  }
}

/**
 * The events out of whatever the timeline wrapped them in.
 *
 * The Hub returns {items, total}; earlier drafts of this view looked for
 * "events" and silently rendered an empty trace against a Hub that had one.
 */
function timelineItems(payload: unknown): unknown {
  if (!payload || typeof payload !== 'object') return null;
  const record = payload as Record<string, unknown>;
  return record['items'] ?? record['events'] ?? null;
}

function summarize(record: Record<string, unknown>): string {
  const details = record['details'];
  if (details && typeof details === 'object') {
    const map = details as Record<string, unknown>;
    const title = text(map['title']) || text(map['message']) || text(map['reason']) || text(map['status']);
    if (title) return title.slice(0, MAX_TRACE);
  }
  return text(record['event_type']).slice(0, MAX_TRACE);
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function numeric(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}
