import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { HubApiCoreService } from '../../services/hub-api-core.service';
import { MeetApiService } from './meet-api.service';
import { clipRequest, MeetStoredClipChoice } from './meet-visual-choice';

describe('Explicit Meet clip turn', () => {
  const choice: MeetStoredClipChoice = { kind: 'stored_clip', artifactId: 'clip', repeatMode: 'hold_last' };
  const core = { request: vi.fn() };

  beforeEach(() => {
    core.request.mockReset().mockReturnValue(of({}));
    TestBed.configureTestingModule({ providers: [
      { provide: HubApiCoreService, useValue: core },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'https://hub.test' }] } },
    ] });
  });

  it('maps only the selected clip fields on the exact task endpoint', () => {
    TestBed.inject(MeetApiService).turn('project', 'Hello', false, 'task:1', choice).subscribe();
    expect(core.request).toHaveBeenCalledWith('POST', 'https://hub.test/api/meet/v1/projects/project/tasks/task%3A1/turns', 'https://hub.test', {
      body: { text: 'Hello', persona_video_id: 'clip', video_repeat_mode: 'hold_last' },
    });
  });

  it('adds publication only when explicitly requested', () => {
    TestBed.inject(MeetApiService).turn('project', 'Hello', true, '', choice).subscribe();
    expect(core.request).toHaveBeenCalledWith('POST', 'https://hub.test/api/meet/v1/projects/project/turns', 'https://hub.test', {
      body: { text: 'Hello', publish_to_meet: true, persona_video_id: 'clip', video_repeat_mode: 'hold_last' },
    });
  });

  it.each([
    null, { ...choice, repeatMode: '' }, { ...choice, artifactId: '/private/clip.mp4' },
    { ...choice, kind: 'camera' }, { ...choice, grant: 'not-authority' },
  ])('fails invalid or broadened choices before HTTP', value => {
    expect(() => clipRequest(value as never)).toThrow();
    const failed = vi.fn();
    TestBed.inject(MeetApiService).turn('project', 'Hello', false, '', value as never).subscribe({ error: failed });
    expect(failed).toHaveBeenCalledOnce();
    expect(core.request).not.toHaveBeenCalled();
  });
});
