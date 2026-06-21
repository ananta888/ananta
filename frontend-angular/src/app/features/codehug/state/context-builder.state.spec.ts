import { TestBed } from '@angular/core/testing';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { of, throwError, firstValueFrom } from 'rxjs';

import { ContextBuilderState } from './context-builder.state';
import { CodeCompassService } from '../services/code-compass.service';
import { ContextPackageService } from '../services/context-package.service';
import { ChServiceError, DEFAULT_SENSITIVE_FILE_PATTERNS } from '../models/codehug.models';

function mockCC() {
  return {
    listFiles: vi.fn(() => of([
      { path: 'src/main.ts', language: 'ts', sizeBytes: 1000, lastModified: 0, symbolIds: [], isSensitive: false },
      { path: '.env', language: 'env', sizeBytes: 200, lastModified: 0, symbolIds: [], isSensitive: true },
    ])),
    getFileContext: vi.fn(() => of({
      file: { path: 'src/main.ts', language: 'ts', sizeBytes: 1000, lastModified: 0, symbolIds: [], isSensitive: false },
      symbols: [
        { id: 'sym-1', name: 'foo', qualifiedName: 'foo', kind: 'function', filePath: 'src/main.ts', lineStart: 1, lineEnd: 5, visibility: 'public' },
      ],
      deterministicFacts: [],
      llmSummary: null,
      llmSummaryConfidence: null,
    })),
    resolveContext: vi.fn(() => of({
      suggestions: [
        { symbolId: 'sym-1', reason: 'matches task', relevanceScore: 0.9, source: 'resolve_context' },
      ],
      resolvedSymbols: [],
      estimatedTokenCount: 100,
    })),
  };
}
function mockPackages() {
  return {
    listForProject: vi.fn(() => of([])),
    create: vi.fn(() => of({ id: 'pkg-1', name: 'test', version: 1, filePaths: ['src/main.ts'], symbolIds: [], reasons: {}, estimatedTokenCount: 250, createdAt: 0, updatedAt: 0, projectId: 'p1' })),
    classifySensitiveFiles: vi.fn((paths: string[]) =>
      paths.map(p => ({
        filePath: p,
        matchedPattern: p === '.env' ? '.env' : null,
        decision: p === '.env' ? 'requires-confirmation' : 'auto-exclude',
      }))
    ),
  };
}

describe('ContextBuilderState', () => {
  let state: ContextBuilderState;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        ContextBuilderState,
        { provide: CodeCompassService, useValue: mockCC() },
        { provide: ContextPackageService, useValue: mockPackages() },
      ],
    });
    state = TestBed.inject(ContextBuilderState);
  });

  it('starts empty', () => {
    expect(state.currentProjectId()).toBeNull();
    expect(state.hasSelection()).toBe(false);
    expect(state.estimatedTokenCount()).toBe(0);
  });

  it('setProject loads files', () => {
    state.setProject('p1');
    expect(state.currentProjectId()).toBe('p1');
    expect(state.files().length).toBe(2);
    expect(state.sensitiveDecisions()['.env']?.decision).toBe('requires-confirmation');
  });

  it('toggleFile adds and removes', () => {
    state.setProject('p1');
    state.toggleFile('src/main.ts', true);
    expect(state.selectedFilePaths()).toContain('src/main.ts');
    expect(state.hasSelection()).toBe(true);
    state.toggleFile('src/main.ts', false);
    expect(state.selectedFilePaths()).not.toContain('src/main.ts');
  });

  it('estimatedTokenCount sums sizeBytes / 4', () => {
    state.setProject('p1');
    state.toggleFile('src/main.ts', true);
    expect(state.estimatedTokenCount()).toBe(250); // 1000/4
  });

  it('resolveContext sets suggestions', () => {
    state.setProject('p1');
    state.setTaskDescription('find foo');
    state.resolveContext();
    expect(state.suggestions()?.suggestions.length).toBe(1);
  });

  it('resolveContext is no-op when task is empty', () => {
    state.setProject('p1');
    state.resolveContext();
    expect(state.suggestions()).toBeNull();
  });

  it('acceptSuggestion adds to selection', () => {
    state.setProject('p1');
    state.setTaskDescription('find foo');
    state.resolveContext();
    state.acceptSuggestion('sym-1', 'src/main.ts');
    expect(state.selectedSymbolIds()).toContain('sym-1');
    expect(state.selectedFilePaths()).toContain('src/main.ts');
  });

  it('loadSymbolsForFile populates symbols map', () => {
    state.setProject('p1');
    state.loadSymbolsForFile('src/main.ts');
    expect(state.symbolsByFile().get('src/main.ts')?.length).toBe(1);
  });

  it('saveCurrent throws ChServiceError without project', () => {
    expect(() => state.saveCurrent()).toThrow(ChServiceError);
  });

  it('saveCurrent throws ChServiceError without name', () => {
    state.setProject('p1');
    state.toggleFile('src/main.ts', true);
    expect(() => state.saveCurrent()).toThrow(/Name/);
  });

  it('saveCurrent throws ChServiceError without selection', () => {
    state.setProject('p1');
    state.setPackageName('test');
    expect(() => state.saveCurrent()).toThrow(/keine/i);
  });

  it('saveCurrent succeeds with full data', async () => {
    state.setProject('p1');
    state.setPackageName('test-pkg');
    state.toggleFile('src/main.ts', true);
    const result = await firstValueFrom(state.saveCurrent());
    expect(result.id).toBe('pkg-1');
  });

  it('rejectSensitiveFile removes from selection', () => {
    state.setProject('p1');
    state.toggleFile('.env', true);
    expect(state.selectedFilePaths()).toContain('.env');
    state.rejectSensitiveFile('.env');
    expect(state.selectedFilePaths()).not.toContain('.env');
  });
});