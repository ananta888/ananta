import type { KanbanCard } from './kanban-api.client';
import { isRevisionConflict, optimisticMove } from './kanban.store';

const card = (id: string, column_id: KanbanCard['column_id'], position: number): KanbanCard => ({
  schema_version: 'kanban.v1',
  id,
  board_id: 'hub',
  title: id,
  description: null,
  status: column_id,
  column_id,
  position,
  revision: 3,
  priority: 'Medium',
  assignee: null,
  labels: [],
  blocked: false,
  dependencies: [],
  comment_count: 0,
  activity_count: 0,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
});

describe('Kanban optimistic projection', () => {
  it('moves immutable card snapshots and keeps server revisions untouched', () => {
    const source = [card('a', 'todo', 0), card('b', 'in_progress', 0)];
    const moved = optimisticMove(source, 'a', 'in_progress', 1);
    expect(source[0].column_id).toBe('todo');
    expect(moved.find(item => item.id === 'a')).toMatchObject({
      column_id: 'in_progress',
      position: 1,
      revision: 3,
    });
  });

  it('recognizes only HTTP 409 as a snapshot conflict', () => {
    expect(isRevisionConflict({ status: 409 })).toBe(true);
    expect(isRevisionConflict({ status: 403 })).toBe(false);
  });
});

