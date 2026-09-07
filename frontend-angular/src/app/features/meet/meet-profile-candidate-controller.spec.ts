import { NEVER, Subject } from 'rxjs';
import type { PersonaEffectiveProfile } from '../organizations/persona-media/persona-profile.models';
import { scope } from './meet-avatar-test-fixtures';
import { MeetProfileCandidateController } from './meet-profile-candidate-controller';
import { voiceCandidate } from './meet-voice-candidate';
import { effectiveVoice } from './meet-voice-test-fixtures';

describe('bounded candidate controller', () => {
  it('times out without human input, automatic retries or fallback', () => {
    vi.useFakeTimers();
    try {
      const load = vi.fn(() => NEVER), c = new MeetProfileCandidateController(load, voiceCandidate, () => scope);
      c.resolve(); expect(c.busy()).toBe(true); vi.advanceTimersByTime(10_001);
      expect(c.busy()).toBe(false); expect(c.current()).toBeNull(); expect(load).toHaveBeenCalledTimes(1);
      expect(c.message()).toContain('Keine Ersatzquelle'); c.clear();
    } finally { vi.useRealTimers(); }
  });
  it('discards late responses from a changed Hub even without a new request', () => {
    const pending = new Subject<PersonaEffectiveProfile>(); let current = scope;
    const c = new MeetProfileCandidateController(() => pending, voiceCandidate, () => current);
    c.resolve(); current = { ...scope, hub: 'https://changed.test' }; pending.next(effectiveVoice());
    expect(c.current()).toBeNull(); expect(c.busy()).toBe(false); c.clear();
  });
});
