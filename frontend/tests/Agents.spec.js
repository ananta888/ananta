import { mount, flushPromises } from '@vue/test-utils';
import { describe, it, expect, vi } from 'vitest';
import Agents from '../src/components/Agents.vue';

describe('Agents.vue', () => {
  it('loads agents and enters edit mode', async () => {
    const mockConfig = { agents: { Bob: { model: 'm1', provider: 'p1' } } };
    const fetchMock = vi.fn(() => Promise.resolve({ json: () => Promise.resolve(mockConfig) }));
    const originalFetch = global.fetch;
    global.fetch = fetchMock;

    const wrapper = mount(Agents);
    await flushPromises();

    expect(fetchMock).toHaveBeenCalled();
    expect(wrapper.text()).toContain('Bob');

    await wrapper.find('button').trigger('click');
    expect(wrapper.find('input').exists()).toBe(true);

    global.fetch = originalFetch;
  });
});
