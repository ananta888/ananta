import { mount, flushPromises } from '@vue/test-utils';
import { describe, it, expect, vi } from 'vitest';
import Endpoints from '../src/components/Endpoints.vue';

describe('Endpoints.vue', () => {
  it('loads endpoints and enters edit mode', async () => {
    const mockConfig = { api_endpoints: [{ type: 't1', url: 'u1' }] };
    const fetchMock = vi.fn(() => Promise.resolve({ json: () => Promise.resolve(mockConfig) }));
    const originalFetch = global.fetch;
    global.fetch = fetchMock;

    const wrapper = mount(Endpoints);
    await flushPromises();

    expect(fetchMock).toHaveBeenCalled();
    expect(wrapper.text()).toContain('t1');

    await wrapper.find('button').trigger('click');
    expect(wrapper.find('input').exists()).toBe(true);

    global.fetch = originalFetch;
  });
});
