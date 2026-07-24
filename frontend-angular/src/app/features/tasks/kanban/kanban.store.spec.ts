import { firstValueFrom, of, throwError } from 'rxjs';

import {
  KANBAN_CARD_PAGE_LIMIT,
  KANBAN_CARD_PAGE_SIZE,
  KanbanCard,
  KanbanCardPage,
  loadAllKanbanCardPages,
} from './kanban-api.client';
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

function page(index: number, next_cursor: string | null): KanbanCardPage {
  return {
    board_id: 'hub',
    board_revision: 'snapshot-1',
    items: Array.from(
      { length: KANBAN_CARD_PAGE_SIZE },
      (_, offset) => card(`card-${index * KANBAN_CARD_PAGE_SIZE + offset}`, 'todo', offset),
    ),
    next_cursor,
  };
}

describe('Kanban bounded card pagination', () => {
  it('loads ten cursor pages atomically up to the 1000-card boundary', async () => {
    const cursors: Array<string | undefined> = [];
    const result = await firstValueFrom(loadAllKanbanCardPages(cursor => {
      cursors.push(cursor);
      const index = cursor ? Number(cursor.slice('page-'.length)) : 0;
      return of(page(
        index,
        index + 1 < KANBAN_CARD_PAGE_LIMIT ? `page-${index + 1}` : null,
      ));
    }));

    expect(cursors).toHaveLength(KANBAN_CARD_PAGE_LIMIT);
    expect(result.items).toHaveLength(1_000);
    expect(result.items[999].id).toBe('card-999');
    expect(result.next_cursor).toBeNull();
  });

  it('fails closed on a repeated cursor', async () => {
    await expect(firstValueFrom(loadAllKanbanCardPages(cursor =>
      of(page(cursor ? 1 : 0, 'page-1')),
    ))).rejects.toThrow('kanban_card_cursor_loop');
  });

  it('fails closed when another page follows the ten-page boundary', async () => {
    await expect(firstValueFrom(loadAllKanbanCardPages(cursor => {
      const index = cursor ? Number(cursor.slice('page-'.length)) : 0;
      return of(page(index, `page-${index + 1}`));
    }))).rejects.toThrow('kanban_card_page_limit_exceeded');
  });

  it('does not emit a partial snapshot when a later page fails', async () => {
    await expect(firstValueFrom(loadAllKanbanCardPages(cursor =>
      cursor
        ? throwError(() => new Error('later_page_failed'))
        : of(page(0, 'page-1')),
    ))).rejects.toThrow('later_page_failed');
  });
});
