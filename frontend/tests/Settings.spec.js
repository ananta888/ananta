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
    await wrapper.find('[data-test="save"]').trigger('click');
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const body = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(body).toEqual({ active_agent: 'Alice' });

    global.fetch = originalFetch;
  });
});
