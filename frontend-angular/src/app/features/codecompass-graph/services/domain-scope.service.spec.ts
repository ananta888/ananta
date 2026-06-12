import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';
import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { AiSnakeConfigService } from '../../../services/ai-snake-config.service';
import { DomainScopeService } from './domain-scope.service';

describe('DomainScopeService', () => {
  let service: DomainScopeService;
  let core: { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn> };
  let dir: { list: ReturnType<typeof vi.fn> };
  let snakeConfig: { config$: { value: Record<string, string> }; updateField: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    core = { get: vi.fn(), post: vi.fn() };
    dir = { list: vi.fn(() => [{ name: 'hub', role: 'hub', url: 'http://hub:5000' }]) };
    snakeConfig = {
      config$: { value: { chat_retrieval_domain_hint: '' } },
      updateField: vi.fn(),
    };

    TestBed.configureTestingModule({
      providers: [
        DomainScopeService,
        { provide: HubApiCoreService, useValue: core },
        { provide: AgentDirectoryService, useValue: dir },
        { provide: AiSnakeConfigService, useValue: snakeConfig },
      ],
    });
    service = TestBed.inject(DomainScopeService);
  });

  describe('loadDomains', () => {
    it('loads domains from hub API and updates subjects', () => {
      const response = {
        data: {
          domains: [{ domain_id: 'orders', display_name: 'Bestellmodul', confidence: 0.85, root_paths: ['orders/'], boundary_warnings: [], has_descriptor: false }],
          errors: [],
          artifact_path: 'artifacts/codecompass/domains.detected.json',
          scope_enabled: true,
        },
      };
      core.get.mockReturnValue(of(response));

      service.loadDomains();

      expect(core.get).toHaveBeenCalledWith('http://hub:5000/api/codecompass/domains', 'http://hub:5000');
      expect(service.domains$.value).toEqual(response.data.domains);
      expect(service.scopeEnabled$.value).toBe(true);
      expect(service.listErrors$.value).toEqual([]);
    });

    it('does nothing when no hub URL is available', () => {
      dir.list.mockReturnValue([]);

      service.loadDomains();

      expect(core.get).not.toHaveBeenCalled();
      expect(service.domains$.value).toEqual([]);
    });

    it('handles API errors gracefully', () => {
      core.get.mockReturnValue(throwError(() => new Error('API error')));

      service.loadDomains();

      expect(service.domains$.value).toEqual([]);
    });
  });

  describe('previewScope', () => {
    it('returns preview for given domain ids', done => {
      const preview = {
        data: {
          active: true,
          strict: true,
          selected_domain_ids: ['orders'],
          allowed_read_paths: ['orders/'],
          allowed_write_paths: ['orders/'],
          source_domains: [],
          warnings: [],
          violations: [],
          provenance: ['orders<-detected:...'],
        },
      };
      core.post.mockReturnValue(of(preview));

      service.previewScope(['orders']).subscribe(result => {
        expect(core.post).toHaveBeenCalledWith(
          'http://hub:5000/api/codecompass/domain-scope/preview',
          { selected_domain_ids: ['orders'], strict: true },
          'http://hub:5000',
        );
        expect(result).toEqual(preview.data);
        done();
      });
    });

    it('returns null on API error', done => {
      core.post.mockReturnValue(throwError(() => new Error('API error')));

      service.previewScope(['orders']).subscribe(result => {
        expect(result).toBeNull();
        done();
      });
    });
  });

  describe('currentSelection', () => {
    it('returns domain id from domain:-prefixed hint', () => {
      snakeConfig.config$.value.chat_retrieval_domain_hint = 'domain:orders';
      expect(service.currentSelection()).toBe('orders');
    });

    it('returns null for unprefixed hint', () => {
      snakeConfig.config$.value.chat_retrieval_domain_hint = 'codecompass';
      expect(service.currentSelection()).toBeNull();
    });

    it('returns null for empty hint', () => {
      snakeConfig.config$.value.chat_retrieval_domain_hint = '';
      expect(service.currentSelection()).toBeNull();
    });
  });

  describe('selectDomain', () => {
    it('writes domain:<id> to config when setting a domain', () => {
      service.selectDomain('orders');
      expect(snakeConfig.updateField).toHaveBeenCalledWith('chat_retrieval_domain_hint', 'domain:orders');
    });

    it('clears the hint when null is passed', () => {
      service.selectDomain(null);
      expect(snakeConfig.updateField).toHaveBeenCalledWith('chat_retrieval_domain_hint', '');
    });
  });
});
