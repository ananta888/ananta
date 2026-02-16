import { TaskStatusDisplayPipe } from './task-status-display.pipe';

describe('TaskStatusDisplayPipe', () => {
  let pipe: TaskStatusDisplayPipe;

  beforeEach(() => {
    pipe = new TaskStatusDisplayPipe();
  });

  it('maps legacy aliases to canonical display labels', () => {
    expect(pipe.transform('done')).toBe('Completed');
    expect(pipe.transform('in-progress')).toBe('In Progress');
    expect(pipe.transform('to-do')).toBe('To Do');
    expect(pipe.transform('backlog')).toBe('To Do');
  });

  it('returns known canonical labels', () => {
    expect(pipe.transform('todo')).toBe('To Do');
    expect(pipe.transform('in_progress')).toBe('In Progress');
    expect(pipe.transform('completed')).toBe('Completed');
    expect(pipe.transform('failed')).toBe('Failed');
  });

  it('falls back to normalized text for unknown statuses', () => {
    expect(pipe.transform('needs_review')).toBe('needs review');
    expect(pipe.transform('qa-pending')).toBe('qa pending');
  });

  it('defaults empty values to To Do', () => {
    expect(pipe.transform(undefined)).toBe('To Do');
    expect(pipe.transform(null)).toBe('To Do');
    expect(pipe.transform('')).toBe('To Do');
  });
});
