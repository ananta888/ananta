import { androidRuntimeAgents, usesEmbeddedAndroidHub } from './agent-directory.service';

describe('androidRuntimeAgents', () => {
  it('keeps an explicitly configured remote Hub URL and credentials', () => {
    const agents = androidRuntimeAgents([
      { name: 'hub', role: 'hub', url: 'https://ananta.example.test', token: 'jwt' },
      { name: 'alpha', role: 'worker', url: 'https://worker.example.test' },
    ]);

    expect(agents[0]).toEqual({
      name: 'hub',
      role: 'hub',
      url: 'https://ananta.example.test',
      token: 'jwt',
    });
    expect(agents[1]?.url).toBe('http://127.0.0.1:5000');
  });

  it('uses the embedded Hub when no Hub has been configured', () => {
    expect(androidRuntimeAgents([])[0]?.url).toBe('http://127.0.0.1:5000');
  });
});

describe('usesEmbeddedAndroidHub', () => {
  it('distinguishes the embedded loopback Hub from an Android-reachable remote Hub', () => {
    expect(usesEmbeddedAndroidHub('http://127.0.0.1:5000')).toBe(true);
    expect(usesEmbeddedAndroidHub('http://localhost:5000/')).toBe(true);
    expect(usesEmbeddedAndroidHub('https://ananta.example.org')).toBe(false);
    expect(usesEmbeddedAndroidHub('http://192.168.1.20:5000')).toBe(false);
  });
});
