import { ComponentFixture, TestBed } from '@angular/core/testing';
import { BehaviorSubject, of } from 'rxjs';

import { ControlCenterStateFacade } from '../services/control-center-state.facade';
import { ScrumImprovementApiService } from './scrum-improvement-api.service';
import { ScrumImprovementComponent } from './scrum-improvement.component';

describe('ScrumImprovementComponent', () => {
  let fixture: ComponentFixture<ScrumImprovementComponent>;
  const state = {
    selectedProjectId$: new BehaviorSubject('project-1'),
    hubBaseUrl: vi.fn(() => 'http://hub.test'),
  };
  const api = {
    overview: vi.fn(() => of({
      schema: 'ananta.scrum-continuous-improvement-overview.v1',
      scope_id: 'project-1',
      sprints: [{
        sprint_id: 'sprint-2', sequence: 2, sprint_goal: 'Reduce rework', lifecycle_state: 'active',
        architecture_handoff: { architecture_revision_id: 'arch-2' },
        improvement_commitment_ids: ['commitment-1'], scope_changes: [],
      }],
      architecture_baselines: [{
        revision_id: 'arch-2', lifecycle_state: 'active', parent_revision_id: 'arch-1', guardrail_digest: 'a'.repeat(64),
      }],
      improvement_commitments: [{
        commitment_id: 'commitment-1', status: 'accepted', owner_role: 'scrum_master', metric_names: ['rework'],
      }],
      improvement_effects: [{
        evaluation_id: 'effect-1', commitment_id: 'commitment-1', outcome: 'improved',
      }],
      architecture_effects: [],
      counts: { sprints: 1, active_architecture_baselines: 1, accepted_commitments: 1, rolled_back_commitments: 0 },
    })),
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ScrumImprovementComponent],
      providers: [
        { provide: ControlCenterStateFacade, useValue: state },
        { provide: ScrumImprovementApiService, useValue: api },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(ScrumImprovementComponent);
    fixture.detectChanges();
  });

  it('renders all three feedback loops and their bound revisions', () => {
    const text = fixture.nativeElement.textContent;
    expect(text).toContain('In-Sprint Inspect & Adapt');
    expect(text).toContain('Architecture Feedback');
    expect(text).toContain('Retrospective & Commitments');
    expect(text).toContain('arch-2');
    expect(text).toContain('commitment-1');
    expect(api.overview).toHaveBeenCalledWith('http://hub.test', 'project-1');
  });
});
