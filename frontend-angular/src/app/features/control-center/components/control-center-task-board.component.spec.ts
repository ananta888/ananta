import { TestBed } from '@angular/core/testing';
import { BehaviorSubject } from 'rxjs';

import { ControlCenterTaskBoardComponent } from './control-center-task-board.component';
import { ControlCenterStateFacade } from '../services/control-center-state.facade';

class MockStateFacade {
  tasks$ = new BehaviorSubject<any[]>([
    { id: 't1', title: 'Task 1', description: '', status: 'todo', priority: 'High' },
    { id: 't2', title: 'Task 2', description: '', status: 'in_progress', priority: 'Low' },
  ]);
  loading$ = new BehaviorSubject<boolean>(false);
  loadTasks = jasmine.createSpy('loadTasks');
}

describe('ControlCenterTaskBoardComponent', () => {
  it('maps backend statuses to board columns', async () => {
    await TestBed.configureTestingModule({
      imports: [ControlCenterTaskBoardComponent],
      providers: [{ provide: ControlCenterStateFacade, useClass: MockStateFacade }],
    }).compileComponents();

    const fixture = TestBed.createComponent(ControlCenterTaskBoardComponent);
    fixture.detectChanges();
    const cmp = fixture.componentInstance;

    expect(cmp.byStatus('backlog').length).toBe(1);
    expect(cmp.byStatus('running').length).toBe(1);
  });
});
