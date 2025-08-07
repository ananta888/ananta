import { mount, flushPromises } from '@vue/test-utils';
import { describe, it, expect, vi } from 'vitest';
import AgentLogViewer from '../src/components/AgentLogViewer.vue';

describe('AgentLogViewer.vue', () => {
  it('lädt Agenten und Logs', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((url, opts) => {
      if (url === '/config') {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ agents: { default: {}, other: {} } }) });
      }
      if (url === '/agent/default/log' && (!opts || opts.method !== 'DELETE')) {
        return Promise.resolve({ ok: true, text: () => Promise.resolve('2024 INFO test') });
      }
      if (url === '/agent/default/log' && opts && opts.method === 'DELETE') {
        return Promise.resolve({ ok: true });
      }
      if (url === '/agent/default/tasks') {
         return Promise.resolve({ ok: true, json: () => Promise.resolve({ current_task: 'c', tasks: [{ task: 'p' }] }) });
      }
      return Promise.resolve({ ok: true, text: () => Promise.resolve('') });
    });
    const originalFetch = global.fetch;
    global.fetch = fetchMock;

    const wrapper = mount(AgentLogViewer);
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledWith('/config');
    expect(fetchMock).toHaveBeenCalledWith('/agent/default/log');
    expect(fetchMock).toHaveBeenCalledWith('/agent/default/tasks');
    expect(wrapper.find('.log-entry').text()).toContain('test');
    expect(wrapper.text()).toContain('Aktueller Task: c');

    await wrapper.find('[data-test="clear-log"]').trigger('click');
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledWith('/agent/default/log', { method: 'DELETE' });
    expect(wrapper.findAll('.log-entry').length).toBe(0);

    wrapper.unmount();
    global.fetch = originalFetch;
    vi.useRealTimers();
  });
});
