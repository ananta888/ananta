import { mount, flushPromises } from '@vue/test-utils';
import { describe, it, expect, vi } from 'vitest';
import Settings from '../src/components/Settings.vue';

describe('Settings.vue', () => {
  it('loads and saves active agent', async () => {
    const mockConfig = { active_agent: 'Bob', agents: { Bob: {}, Alice: {} } };
    const fetchMock = vi.fn((url, opts) => {
      if (!opts) {
        return Promise.resolve({ json: () => Promise.resolve(mockConfig) });
      }
      return Promise.resolve({});
    });
    const originalFetch = global.fetch;
    global.fetch = fetchMock;

    const wrapper = mount(Settings);
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledWith('/config');
    expect(wrapper.find('select').element.value).toBe('Bob');

    await wrapper.find('select').setValue('Alice');
    await wrapper.find('button').trigger('click');
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/config/active_agent',
      expect.objectContaining({ method: 'POST' })
    );

    global.fetch = originalFetch;
  });
});
