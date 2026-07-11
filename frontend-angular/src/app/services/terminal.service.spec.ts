import { TestBed } from '@angular/core/testing';
import { AgentDirectoryService } from './agent-directory.service';
import { TerminalService } from './terminal.service';
import { UserAuthService } from './user-auth.service';

describe('TerminalService', () => {
  let service: TerminalService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        TerminalService,
        { provide: AgentDirectoryService, useValue: { list: () => [] } },
        { provide: UserAuthService, useValue: { token: '' } },
      ],
    });
    service = TestBed.inject(TerminalService);
  });

  it('keeps an actionable server error over a later websocket transport close', () => {
    const prefer = (service as any).preferConnectionError.bind(service);

    expect(prefer('terminal_disabled', 'closed_before_ready(code=1006)')).toBe('terminal_disabled');
    expect(prefer('closed_before_ready(code=1006)', 'terminal_disabled')).toBe('terminal_disabled');
  });
});
