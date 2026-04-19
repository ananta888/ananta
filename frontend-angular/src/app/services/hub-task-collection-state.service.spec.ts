import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { HubApiService } from './hub-api.service';
import { HubTaskCollectionStateService } from './hub-task-collection-state.service';

describe('HubTaskCollectionStateService', () => {
  let service: HubTaskCollectionStateService;
  let hubApi: { listTasks: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    hubApi = {
      listTasks: vi.fn(() => of([{ id: 'T-1', status: 'todo' }, { id: 'T-2', parent_task_id: 'T-1', status: 'in_progress' }])),
    };

    TestBed.configureTestingModule({
      providers: [
        HubTaskCollectionStateService,
        { provide: HubApiService, useValue: hubApi },
      ],
    });
    service = TestBed.inject(HubTaskCollectionStateService);
  });

  it('loads and exposes shared task snapshots', () => {
    service.connect('http://hub:5000', 3000);

    expect(hubApi.listTasks).toHaveBeenCalledWith('http://hub:5000');
    expect(service.tasks()).toHaveLength(2);
    expect(service.childrenOf('T-1')).toHaveLength(1);
    expect(service.snapshot().counts).toEqual({ todo: 1, in_progress: 1 });
    expect(service.snapshot().asyncState).toEqual(expect.objectContaining({
      data: service.tasks(),
      empty: false,
      error: null,
      loading: false,
      refreshing: false,
    }));
    expect(service.lastLoadedAt()).not.toBeNull();
    expect(service.loading()).toBe(false);
  });
});
