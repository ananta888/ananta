import { mount, flushPromises } from '@vue/test-utils';
import { describe, it, expect, vi } from 'vitest';
import Settings from '../src/components/Settings.vue';

describe('Settings.vue', () => {
  it('loads and saves active agent', async () => {
    const mockConfig = { active_agent: 'Bob', agents: { Bob: {}, Alice: {} } };
    const fetchMock = vi.fn((url, opts) => {
      if (!opts && url === '/config') {
        return Promise.resolve({ json: () => Promise.resolve(mockConfig) });
      }
      if (!opts && url === '/agent/config') {
        return Promise.resolve({ json: () => Promise.resolve({}) });
      }
      return Promise.resolve({});
    });
    const originalFetch = global.fetch;
    global.fetch = fetchMock;

    const wrapper = mount(Settings);
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledWith('/config');
    expect(fetchMock).toHaveBeenCalledWith('/agent/config');
    expect(wrapper.find('select').element.value).toBe('Bob');

    await wrapper.find('select').setValue('Alice');
    await wrapper.find('[data-test="save"]').trigger('click');
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const body = JSON.parse(fetchMock.mock.calls[2][1].body);
    expect(body).toEqual({ active_agent: 'Alice' });

    global.fetch = originalFetch;
  });

  it('clears controller log', async () => {
    const fetchMock = vi.fn((url, opts) => {
      if (!opts && url === '/config') {
        return Promise.resolve({ json: () => Promise.resolve({ active_agent: '', agents: {} }) });
      }
      if (!opts && url === '/agent/config') {
        return Promise.resolve({ json: () => Promise.resolve({}) });
      }
      if (!opts && url === '/controller/status') {
        return Promise.resolve({ json: () => Promise.resolve(['a', 'b']) });
      }
      if (url === '/controller/status' && opts && opts.method === 'DELETE') {
        return Promise.resolve({ ok: true });
      }
      return Promise.resolve({});
    });
    const originalFetch = global.fetch;
    global.fetch = fetchMock;

    const wrapper = mount(Settings);
    await flushPromises();

    await wrapper.find('[data-test="load-log"]').trigger('click');
    await flushPromises();
    expect(wrapper.html()).toContain('a\nb');

    await wrapper.find('[data-test="clear-log"]').trigger('click');
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledWith('/controller/status', { method: 'DELETE' });
    expect(wrapper.html()).not.toContain('a\nb');

    global.fetch = originalFetch;
  });
});
