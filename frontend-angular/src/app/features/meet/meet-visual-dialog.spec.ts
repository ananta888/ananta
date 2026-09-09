import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { HubApiCoreService } from '../../services/hub-api-core.service';
import { MeetDialogApiService, validateDialog } from './meet-dialog-api.service';

const control = () => ({ enabled: false, revision: 1, since: 1000 });
const visualDialog = () => ({
  schema: 'ananta.meet-dialog-status.v1' as const, task_id: 'task', status: 'in_progress', deadline: 1788730000,
  capabilities: ['video.receive'], controls: { revision: 1, chat: control(), audio: control(), screen: control(), visual: control() },
});

describe('optional bounded visual reception in the Hub response', () => {
  it('accepts the exact current Hub visual task without requiring chat or publication', () => {
    const value = visualDialog(); expect(validateDialog(value)).toBe(value);
  });
  it('keeps legacy tasks unchanged and accepts mixed visual lists and terminal stop responses', async () => {
    const value = visualDialog();
    const legacy = { ...value, task_id: 'legacy', capabilities: ['screen.publish'],
      controls: { revision: 1, chat: control(), audio: control(), screen: control() } };
    expect(validateDialog(legacy)).toBe(legacy); expect(legacy.controls).not.toHaveProperty('visual');
    const list = { schema: 'ananta.meet-dialog-list.v1', items: [legacy, value], next_cursor: null };
    const terminal = { ...value, status: 'cancelled' };
    const request = vi.fn().mockReturnValueOnce(of(list)).mockReturnValueOnce(of(terminal));
    TestBed.configureTestingModule({ providers: [
      { provide: HubApiCoreService, useValue: { request } },
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'https://hub.example.test' }] } },
    ] });
    const api = TestBed.inject(MeetDialogApiService);
    expect(await firstValueFrom(api.list('project'))).toBe(list);
    expect(await firstValueFrom(api.stop('project', 'task'))).toBe(terminal);
    expect(request.mock.calls.map(call => call[0])).toEqual(['GET', 'DELETE']);
    expect(request.mock.calls[1][1]).toBe('https://hub.example.test/api/meet/v1/projects/project/dialogs/task');
  });
  it.each([
    (v: ReturnType<typeof visualDialog>) => ({ ...v, capabilities: [] }),
    (v: ReturnType<typeof visualDialog>) => ({ ...v, capabilities: ['video.receive', 'camera.capture'] }),
    (v: ReturnType<typeof visualDialog>) => ({ ...v, controls: { ...v.controls, visual: null } }),
    (v: ReturnType<typeof visualDialog>) => ({ ...v, controls: { ...v.controls, visual: { ...control(), enabled: 'true' } } }),
    (v: ReturnType<typeof visualDialog>) => ({ ...v, controls: { ...v.controls, visual: { ...control(), revision: 2 } } }),
    (v: ReturnType<typeof visualDialog>) => ({ ...v, controls: { ...v.controls, visual: { ...control(), peer_id: 'other' } } }),
    (v: ReturnType<typeof visualDialog>) => ({ ...v, controls: { ...v.controls, camera: control() } }),
  ])('rejects unnegotiated, broadened or malformed visual control', mutate => {
    expect(() => validateDialog(mutate(visualDialog()) as never)).toThrow('meet_dialog_contract_invalid');
  });
});
