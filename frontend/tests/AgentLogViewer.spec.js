import { mount, flushPromises } from '@vue/test-utils';
import { describe, it, expect, vi } from 'vitest';
import AgentLogViewer from '../src/components/AgentLogViewer.vue';

describe('AgentLogViewer.vue', () => {
  it('lädt Agenten und Logs', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((url) => {
      if (url === '/config') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ agents: { default: {}, other: {} } }) });
      }
      if (url === '/agent/default/log') {
        return Promise.resolve({ ok: true, text: () => Promise.resolve('2024 INFO test') });
      }
      return Promise.resolve({ ok: true, text: () => Promise.resolve('') });
    });
    const originalFetch = global.fetch;
    global.fetch = fetchMock;

    const wrapper = mount(AgentLogViewer);
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledWith('/config');
    expect(fetchMock).toHaveBeenCalledWith('/agent/default/log');
    expect(wrapper.find('.log-entry').text()).toContain('test');

    wrapper.unmount();
    global.fetch = originalFetch;
    vi.useRealTimers();
  });
});
