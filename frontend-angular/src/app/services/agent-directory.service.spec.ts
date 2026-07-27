import {
  androidRuntimeAgents,
  composeRuntimeAgents,
  hostBasedAgentOrigin,
  normalizeHubOrigin,
  usesEmbeddedAndroidHub,
} from './agent-directory.service';

describe('hostBasedAgentOrigin', () => {
  it('keeps browser HTTPS when resolving LAN agent ports', () => {
    expect(hostBasedAgentOrigin('https:', '192.168.178.103', 5000))
      .toBe('https://192.168.178.103:5000');
  });

  it('defaults unknown protocols to HTTP and brackets IPv6 hosts', () => {
    expect(hostBasedAgentOrigin('file:', '::1', 5000)).toBe('http://[::1]:5000');
  });
});

describe('normalizeHubOrigin', () => {
  it('normalizes valid HTTP(S) origins and removes the trailing slash', () => {
    expect(normalizeHubOrigin(' http://10.0.2.2:5000/ ')).toBe('http://10.0.2.2:5000');
    expect(normalizeHubOrigin('https://Hub.Example.test:443/')).toBe('https://hub.example.test');
    expect(normalizeHubOrigin('http://192.168.1.20:5000')).toBe('http://192.168.1.20:5000');
  });

  it.each([
    '',
    'hub.example.test:5000',
    'ftp://hub.example.test',
    'https://user:secret@hub.example.test',
    'https://@hub.example.test',
    'https://hub.example.test/api',
    'https://hub.example.test/.',
    'https://hub.example.test?token=secret',
    'https://hub.example.test?',
    'https://hub.example.test#fragment',
    'https://hub.example.test#',
  ])('rejects a non-origin or credential-bearing value: %s', (value) => {
    expect(normalizeHubOrigin(value)).toBeNull();
  });
});

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

describe('composeRuntimeAgents', () => {
  it('preserves credentials already bound to canonical compose service URLs', () => {
    expect(composeRuntimeAgents([
      { name: 'hub', role: 'hub', url: 'http://ai-agent-hub:5000', token: 'hub-secret' },
      { name: 'alpha', role: 'worker', url: 'http://ai-agent-alpha:5000', token: 'alpha-secret' },
    ])).toEqual([
      { name: 'hub', role: 'hub', url: 'http://ai-agent-hub:5000', token: 'hub-secret' },
      { name: 'alpha', role: 'worker', url: 'http://ai-agent-alpha:5000', token: 'alpha-secret' },
    ]);
  });

  it('clears credentials when moving a loopback entry to a compose trust boundary', () => {
    expect(composeRuntimeAgents([
      { name: 'hub', role: 'hub', url: 'http://127.0.0.1:5000', token: 'local-secret' },
    ])).toEqual([
      { name: 'hub', role: 'hub', url: 'http://ai-agent-hub:5000', token: '' },
    ]);
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
