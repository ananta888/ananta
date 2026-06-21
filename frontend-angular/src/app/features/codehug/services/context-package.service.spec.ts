import { TestBed } from '@angular/core/testing';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { firstValueFrom, of } from 'rxjs';

import { ContextPackageService } from './context-package.service';
import { HubApiCoreService } from '../../../services/hub-api-core.service';
import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { ChServiceError, DEFAULT_SENSITIVE_FILE_PATTERNS } from '../models/codehug.models';

function mockHubCore() {
  return {
    get: vi.fn(() => of({})),
    post: vi.fn(() => of({})),
    patch: vi.fn(() => of({})),
    delete: vi.fn(() => of(undefined)),
  };
}

function mockDir() { return { list: () => [{ role: 'hub', url: 'http://hub.test', name: 'h' }] }; }

describe('ContextPackageService', () => {
  let service: ContextPackageService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        ContextPackageService,
        { provide: HubApiCoreService, useValue: mockHubCore() },
        { provide: AgentDirectoryService, useValue: mockDir() },
      ],
    });
    service = TestBed.inject(ContextPackageService);
  });

  it('should be created', () => expect(service).toBeTruthy());

  describe('classifySensitiveFiles', () => {
    it('detects .env', () => {
      const result = service.classifySensitiveFiles(['src/main.py', '.env']);
      expect(result[0].decision).toBe('auto-exclude');
      expect(result[1].decision).toBe('requires-confirmation');
      expect(result[1].matchedPattern).toBe('.env');
    });

    it('detects .env.production via wildcard', () => {
      const result = service.classifySensitiveFiles(['.env.production']);
      expect(result[0].decision).toBe('requires-confirmation');
      expect(result[0].matchedPattern).toBe('.env.*');
    });

    it('detects secrets directory', () => {
      const result = service.classifySensitiveFiles(['config/secrets/api.json']);
      expect(result[0].decision).toBe('requires-confirmation');
    });

    it('detects nested .ssh', () => {
      const result = service.classifySensitiveFiles(['home/user/.ssh/id_rsa']);
      expect(result[0].decision).toBe('requires-confirmation');
    });

    it('non-sensitive file passes', () => {
      const result = service.classifySensitiveFiles(['src/app/main.ts']);
      expect(result[0].decision).toBe('auto-exclude');
      expect(result[0].matchedPattern).toBeNull();
    });

    it('honors custom patterns', () => {
      service.setSensitivePatterns(['*.license']);
      const result = service.classifySensitiveFiles(['x.license', '.env']);
      expect(result[0].decision).toBe('requires-confirmation');
      expect(result[1].decision).toBe('auto-exclude'); // .env no longer in list
    });

    it('falls back to defaults when given empty list', () => {
      service.setSensitivePatterns([]);
      expect(service.getSensitivePatterns()).toEqual(DEFAULT_SENSITIVE_FILE_PATTERNS);
    });
  });

  describe('create', () => {
    it('rejects packages containing sensitive files', () => {
      expect(() => service.create({
        projectId: 'p1',
        name: 'test',
        filePaths: ['.env', 'src/main.py'],
        symbolIds: [],
        reasons: {},
      })).toThrow(ChServiceError);
    });

    it('accepts clean packages', async () => {
      await firstValueFrom(service.create({
        projectId: 'p1',
        name: 'test',
        filePaths: ['src/main.py'],
        symbolIds: [],
        reasons: { 'src/main.py': 'core module' },
      }));
    });
  });

  describe('exportAsJson', () => {
    it('rejects without confirmExport=true', () => {
      expect(() => service.exportAsJson('id-1', false)).toThrow(ChServiceError);
    });
  });
});