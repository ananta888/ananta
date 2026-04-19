import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { HubApiService } from '../../services/hub-api.service';
import { HubLiveStateService } from '../../services/hub-live-state.service';
import { HubTaskCollectionStateService } from '../../services/hub-task-collection-state.service';
import { TaskManagementFacade } from './task-management.facade';

describe('TaskManagementFacade', () => {
  let facade: TaskManagementFacade;
  let taskCollection: {
    connect: ReturnType<typeof vi.fn>;
    disconnect: ReturnType<typeof vi.fn>;
    reload: ReturnType<typeof vi.fn>;
    tasks: ReturnType<typeof vi.fn>;
    loading: ReturnType<typeof vi.fn>;
    lastLoadedAt: ReturnType<typeof vi.fn>;
    error: ReturnType<typeof vi.fn>;
    childrenOf: ReturnType<typeof vi.fn>;
    snapshot: ReturnType<typeof vi.fn>;
  };
  let liveState: {
    ensureSystemEvents: ReturnType<typeof vi.fn>;
    disconnectSystemEvents: ReturnType<typeof vi.fn>;
    systemStreamConnected: ReturnType<typeof vi.fn>;
    lastSystemEvent: ReturnType<typeof vi.fn>;
    watchTaskLogs: ReturnType<typeof vi.fn>;
    taskLogState: ReturnType<typeof vi.fn>;
    stopTaskLogs: ReturnType<typeof vi.fn>;
    shouldRefreshTask: ReturnType<typeof vi.fn>;
    snapshot: ReturnType<typeof vi.fn>;
  };
  let hubApi: {
    getTask: ReturnType<typeof vi.fn>;
    listTasks: ReturnType<typeof vi.fn>;
    createTask: ReturnType<typeof vi.fn>;
    patchTask: ReturnType<typeof vi.fn>;
    assign: ReturnType<typeof vi.fn>;
    propose: ReturnType<typeof vi.fn>;
    execute: ReturnType<typeof vi.fn>;
    reviewTaskProposal: ReturnType<typeof vi.fn>;
    taskLogs: ReturnType<typeof vi.fn>;
    archiveTask: ReturnType<typeof vi.fn>;
    cleanupTasks: ReturnType<typeof vi.fn>;
    listArchivedTasks: ReturnType<typeof vi.fn>;
    restoreTask: ReturnType<typeof vi.fn>;
    deleteArchivedTask: ReturnType<typeof vi.fn>;
    cleanupArchivedTasks: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    taskCollection = {
      connect: vi.fn(),
      disconnect: vi.fn(),
      reload: vi.fn(),
      tasks: vi.fn(() => [{ id: 'T-1', status: 'todo' }]),
      loading: vi.fn(() => false),
      lastLoadedAt: vi.fn(() => 123),
      error: vi.fn(() => null),
      childrenOf: vi.fn(() => [{ id: 'T-2', parent_task_id: 'T-1' }]),
      snapshot: vi.fn(() => ({
        tasks: [{ id: 'T-1' }],
        loading: false,
        refreshing: false,
        empty: false,
        lastLoadedAt: 123,
        error: null,
        asyncState: { data: [{ id: 'T-1' }], loading: false, refreshing: false, empty: false, error: null, lastLoadedAt: 123 },
        counts: { todo: 1 },
      })),
    };
    liveState = {
      ensureSystemEvents: vi.fn(),
      disconnectSystemEvents: vi.fn(),
      systemStreamConnected: vi.fn(() => true),
      lastSystemEvent: vi.fn(() => ({ type: 'token_rotated' })),
      watchTaskLogs: vi.fn(),
      taskLogState: vi.fn(() => ({
        logs: [],
        loading: false,
        refreshing: false,
        empty: true,
        connected: true,
        lastEvent: null,
        error: null,
        asyncState: { data: [], loading: false, refreshing: false, empty: true, error: null, lastLoadedAt: null },
      })),
      stopTaskLogs: vi.fn(),
      shouldRefreshTask: vi.fn(() => true),
      snapshot: vi.fn(() => ({ systemStreamConnected: true, lastSystemEvent: { type: 'token_rotated' }, activeTaskLogStreams: 1 })),
    };
    hubApi = {
      getTask: vi.fn(() => of({ id: 'T-1' })),
      listTasks: vi.fn(() => of([{ id: 'T-1' }])),
      createTask: vi.fn(() => of({ id: 'T-2' })),
      patchTask: vi.fn(() => of({ ok: true })),
      assign: vi.fn(() => of({ ok: true })),
      propose: vi.fn(() => of({ command: 'echo hi' })),
      execute: vi.fn(() => of({ ok: true })),
      reviewTaskProposal: vi.fn(() => of({ ok: true })),
      taskLogs: vi.fn(() => of([])),
      archiveTask: vi.fn(() => of({ ok: true })),
      cleanupTasks: vi.fn(() => of({ archived_count: 1 })),
      listArchivedTasks: vi.fn(() => of([])),
      restoreTask: vi.fn(() => of({ ok: true })),
      deleteArchivedTask: vi.fn(() => of({ ok: true })),
      cleanupArchivedTasks: vi.fn(() => of({ deleted_count: 1 })),
    };

    TestBed.configureTestingModule({
      providers: [
        TaskManagementFacade,
        { provide: HubApiService, useValue: hubApi },
        { provide: HubLiveStateService, useValue: liveState },
        { provide: HubTaskCollectionStateService, useValue: taskCollection },
      ],
    });
    facade = TestBed.inject(TaskManagementFacade);
  });

  it('exposes task collection state through one facade', () => {
    facade.connectTaskCollection('http://hub:5000', 3000);
    facade.reloadTaskCollection();

    expect(taskCollection.connect).toHaveBeenCalledWith('http://hub:5000', 3000);
    expect(taskCollection.reload).toHaveBeenCalled();
    expect(facade.tasks()).toEqual([{ id: 'T-1', status: 'todo' }]);
    expect(facade.tasksLoading()).toBe(false);
    expect(facade.tasksLastLoadedAt()).toBe(123);
    expect(facade.childrenOf('T-1')).toEqual([{ id: 'T-2', parent_task_id: 'T-1' }]);
    expect(facade.taskCollectionSnapshot().counts).toEqual({ todo: 1 });
    expect(facade.taskCollectionSnapshot().asyncState.empty).toBe(false);
  });

  it('delegates task live state and task operations', () => {
    facade.ensureSystemEvents('http://hub:5000');
    facade.watchTaskLogs('http://hub:5000', 'T-1', { reset: true });
    facade.createTask('http://hub:5000', { title: 'New task' }).subscribe();
    facade.archiveTask('http://hub:5000', 'T-1').subscribe();

    expect(liveState.ensureSystemEvents).toHaveBeenCalledWith('http://hub:5000');
    expect(liveState.watchTaskLogs).toHaveBeenCalledWith('http://hub:5000', 'T-1', { reset: true });
    expect(facade.systemStreamConnected()).toBe(true);
    expect(facade.lastSystemEvent()).toEqual({ type: 'token_rotated' });
    expect(facade.liveSnapshot().activeTaskLogStreams).toBe(1);
    expect(hubApi.createTask).toHaveBeenCalledWith('http://hub:5000', { title: 'New task' }, undefined);
    expect(hubApi.archiveTask).toHaveBeenCalledWith('http://hub:5000', 'T-1', undefined);
  });
});
