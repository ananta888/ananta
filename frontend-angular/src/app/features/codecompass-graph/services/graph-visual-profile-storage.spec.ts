import { TestBed } from '@angular/core/testing';
import {
  DEFAULT_GRAPH_VISUAL_PROFILE,
  GRAPH_VISUAL_PROFILE_IMPORT_MAX_BYTES,
  GRAPH_VISUAL_PROFILE_PRESETS,
  graphVisualProfileHash,
} from '../models/graph-visual-profile.model';
import { GraphVisualProfileFacade } from './graph-visual-profile.facade';
import {
  GraphVisualProfileStoragePort,
  InMemoryGraphVisualProfileStorageAdapter,
} from './graph-visual-profile-storage.port';
import {
  BrowserKeyValueStorage,
  LocalStorageGraphVisualProfileAdapter,
} from './local-storage-graph-visual-profile.adapter';

class MapStorage implements BrowserKeyValueStorage {
  readonly values = new Map<string, string>();
  failRead = false;
  failWrite = false;

  getItem(key: string): string | null {
    if (this.failRead) throw new Error('blocked');
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    if (this.failWrite) throw { name: 'QuotaExceededError' };
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

function exercisePort(port: GraphVisualProfileStoragePort): void {
  expect(port.load('repo-a')).toEqual({ ok: true, value: null });
  expect(port.save('repo-a', '{"x":1}').ok).toBe(true);
  expect(port.load('repo-a').value).toBe('{"x":1}');
  expect(port.load('repo-b').value).toBeNull();
  expect(port.remove('repo-a').ok).toBe(true);
  expect(port.load('repo-a').value).toBeNull();
}

describe('Graph visual profile storage', () => {
  it('resolves the viewer facade and shared local adapter through Angular DI', () => {
    TestBed.configureTestingModule({ providers: [GraphVisualProfileFacade] });
    const facade = TestBed.inject(GraphVisualProfileFacade);
    expect(facade.activeProfile()).toBe(DEFAULT_GRAPH_VISUAL_PROFILE);
  });

  it('fulfils the port contract with memory and local-storage adapters', () => {
    exercisePort(new InMemoryGraphVisualProfileStorageAdapter());
    exercisePort(new LocalStorageGraphVisualProfileAdapter(new MapStorage()));
  });

  it('round-trips, reloads and resets a canonical profile per graph context', () => {
    const storage = new InMemoryGraphVisualProfileStorageAdapter();
    const first = new GraphVisualProfileFacade(storage);
    first.load('source-a');
    first.selectPreset('dependencies');
    expect(first.save().ok).toBe(true);
    const canonicalStored = storage.load('source-a').value;
    expect(canonicalStored).toBe(first.exportProfile());

    const reloaded = new GraphVisualProfileFacade(storage);
    expect(reloaded.load('source-a').ok).toBe(true);
    expect(reloaded.profileHash()).toBe(graphVisualProfileHash(GRAPH_VISUAL_PROFILE_PRESETS.dependencies));
    expect(reloaded.exportProfile()).toBe(canonicalStored);
    expect(reloaded.load('source-b').profile).toBe(DEFAULT_GRAPH_VISUAL_PROFILE);

    reloaded.load('source-a');
    expect(reloaded.reset().ok).toBe(true);
    expect(new GraphVisualProfileFacade(storage).load('source-a').profile).toBe(DEFAULT_GRAPH_VISUAL_PROFILE);
  });

  it('keeps active and persisted profiles unchanged after an invalid import', () => {
    const storage = new InMemoryGraphVisualProfileStorageAdapter();
    const facade = new GraphVisualProfileFacade(storage);
    facade.load('source-a');
    facade.selectPreset('structure');
    facade.save();
    facade.selectPreset('importance');
    const activeHash = facade.profileHash();

    const result = facade.importProfile('{"schemaVersion":99}');

    expect(result.ok).toBe(false);
    expect(facade.profileHash()).toBe(activeHash);
    expect(new GraphVisualProfileFacade(storage).load('source-a').profile.profileId).toBe('structure');
  });

  it('isolates unsaved profiles between two viewer-local facades', () => {
    const storage = new InMemoryGraphVisualProfileStorageAdapter();
    const first = new GraphVisualProfileFacade(storage);
    const second = new GraphVisualProfileFacade(storage);
    first.load('same-source');
    second.load('same-source');

    first.selectPreset('scope');
    second.selectPreset('change-risk');

    expect(first.activeProfile().profileId).toBe('scope');
    expect(second.activeProfile().profileId).toBe('change-risk');
    expect(new GraphVisualProfileFacade(storage).load('same-source').profile).toBe(DEFAULT_GRAPH_VISUAL_PROFILE);
  });

  it.each([
    ['prototype pollution', '{"__proto__":{"polluted":true}}', 'dangerous_property'],
    ['CSS URL', JSON.stringify({ ...DEFAULT_GRAPH_VISUAL_PROFILE, domainColorOverrides: { x: 'url(https://bad)' } }), 'invalid_color'],
    ['NaN token as string', JSON.stringify({ ...DEFAULT_GRAPH_VISUAL_PROFILE, nodeMetrics: [{ ...DEFAULT_GRAPH_VISUAL_PROFILE.nodeMetrics[0], weight: 'NaN' }] }), 'number_not_finite'],
    ['Infinity token as string', JSON.stringify({ ...DEFAULT_GRAPH_VISUAL_PROFILE, nodeMetrics: [{ ...DEFAULT_GRAPH_VISUAL_PROFILE.nodeMetrics[0], weight: 'Infinity' }] }), 'number_not_finite'],
    ['unknown key', JSON.stringify({ ...DEFAULT_GRAPH_VISUAL_PROFILE, execute: 'now' }), 'unknown_property'],
    ['unknown version', JSON.stringify({ ...DEFAULT_GRAPH_VISUAL_PROFILE, schemaVersion: 2 }), 'unsupported_schema_version'],
  ])('rejects untrusted import: %s', (_label, payload, reasonCode) => {
    const facade = new GraphVisualProfileFacade(new InMemoryGraphVisualProfileStorageAdapter());
    const result = facade.importProfile(payload);
    expect(result.ok).toBe(false);
    expect(result.issues.some(issue => issue.reasonCode === reasonCode)).toBe(true);
    expect(facade.activeProfile()).toBe(DEFAULT_GRAPH_VISUAL_PROFILE);
    expect(({} as any).polluted).toBeUndefined();
  });

  it('checks file size before reading attacker-controlled content', async () => {
    const facade = new GraphVisualProfileFacade(new InMemoryGraphVisualProfileStorageAdapter());
    const text = vi.fn(async () => JSON.stringify(DEFAULT_GRAPH_VISUAL_PROFILE));

    const result = await facade.importProfileFile({
      size: GRAPH_VISUAL_PROFILE_IMPORT_MAX_BYTES + 1,
      text,
    });

    expect(result.ok).toBe(false);
    expect(result.issues[0].reasonCode).toBe('import_too_large');
    expect(text).not.toHaveBeenCalled();
  });

  it.each([Number.NaN, Number.NEGATIVE_INFINITY, -1])(
    'rejects an invalid file size %s before reading content',
    async size => {
      const facade = new GraphVisualProfileFacade(new InMemoryGraphVisualProfileStorageAdapter());
      const text = vi.fn(async () => JSON.stringify(DEFAULT_GRAPH_VISUAL_PROFILE));

      const result = await facade.importProfileFile({ size, text });

      expect(result.ok).toBe(false);
      expect(result.issues[0].reasonCode).toBe('invalid_import_file');
      expect(text).not.toHaveBeenCalled();
    },
  );

  it('enforces the import limit in UTF-8 bytes and leaves active state unchanged', () => {
    const facade = new GraphVisualProfileFacade(new InMemoryGraphVisualProfileStorageAdapter());
    facade.selectPreset('importance');
    const activeHash = facade.profileHash();
    const oversizedMultibytePayload = '€'.repeat(
      Math.floor(GRAPH_VISUAL_PROFILE_IMPORT_MAX_BYTES / 3) + 1,
    );

    const result = facade.importProfile(oversizedMultibytePayload);

    expect(result.ok).toBe(false);
    expect(result.issues[0].reasonCode).toBe('import_too_large');
    expect(facade.profileHash()).toBe(activeHash);
  });

  it('falls back without blocking on quota, blocked reads and corrupt storage', () => {
    const browserStorage = new MapStorage();
    const local = new LocalStorageGraphVisualProfileAdapter(browserStorage);
    const facade = new GraphVisualProfileFacade(local);
    facade.load('source');
    facade.selectPreset('dependencies');
    browserStorage.failWrite = true;
    expect(facade.save().issues[0].reasonCode).toBe('storage_quota_exceeded');

    browserStorage.failWrite = false;
    browserStorage.failRead = true;
    const blocked = new GraphVisualProfileFacade(local).load('source');
    expect(blocked.ok).toBe(false);
    expect(blocked.profile).toBe(DEFAULT_GRAPH_VISUAL_PROFILE);

    const corrupt = new InMemoryGraphVisualProfileStorageAdapter();
    corrupt.save('source', '{invalid');
    const loaded = new GraphVisualProfileFacade(corrupt).load('source');
    expect(loaded.ok).toBe(false);
    expect(loaded.profile).toBe(DEFAULT_GRAPH_VISUAL_PROFILE);
  });

  it('exports only canonical profile configuration and reimports the same hash', () => {
    const first = new GraphVisualProfileFacade(new InMemoryGraphVisualProfileStorageAdapter());
    first.selectPreset('importance');
    const exported = first.exportProfile();
    const parsed = JSON.parse(exported);

    expect(parsed.nodes).toBeUndefined();
    expect(parsed.edges).toBeUndefined();
    expect(exported).not.toContain('repositoryContent');

    const second = new GraphVisualProfileFacade(new InMemoryGraphVisualProfileStorageAdapter());
    expect(second.importProfile(exported).ok).toBe(true);
    expect(second.profileHash()).toBe(first.profileHash());
  });
});
