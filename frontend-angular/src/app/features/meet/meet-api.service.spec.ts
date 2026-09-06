import { MeetBinding, validateMeetBinding } from './meet-api.service';

function binding(): MeetBinding {
  return {
    schema: 'ananta.meet-binding.v1', project_id: 'project-a', task_id: null,
    revision: 1, invite_url: 'https://webrtc.ananta.de/?room=room-0123456789abcdef01&mode=room',
    room_verified: false, membership_granted: false,
    profile: { origin: 'https://webrtc.ananta.de', create_url: 'https://webrtc.ananta.de/',
      creation_mode: 'meet_ui_then_attach' },
  };
}

describe('Meet binding navigation boundary', () => {
  it('accepts the exact configured origin without granting membership', () => {
    expect(validateMeetBinding(binding(), 'project-a', '').membership_granted).toBe(false);
  });
  it.each([
    'https://evil.test/?room=room-0123456789abcdef01&mode=room',
    'javascript:alert(1)',
    'https://webrtc.ananta.de/?room=room-0123456789abcdef01&mode=room&token=secret',
    'https://webrtc.ananta.de/?room=room-0123456789abcdef01&mode=room#secret',
  ])('rejects foreign or broadened invite %s', invite => {
    expect(() => validateMeetBinding({ ...binding(), invite_url: invite }, 'project-a', '')).toThrow();
  });
  it('rejects stale context responses and forged authorization', () => {
    expect(() => validateMeetBinding(binding(), 'project-b', '')).toThrow();
    expect(() => validateMeetBinding(binding(), 'project-a', 'task-a')).toThrow();
    expect(() => validateMeetBinding({ ...binding(), membership_granted: true } as never, 'project-a', '')).toThrow();
  });
  it('rejects unsafe create destinations', () => {
    const value = binding();
    value.profile.create_url = 'https://evil.test/';
    expect(() => validateMeetBinding(value, 'project-a', '')).toThrow();
  });
});
