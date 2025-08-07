import { mount, flushPromises } from '@vue/test-utils';
import { describe, it, expect, vi } from 'vitest';
import AgentTaskOverview from '../src/components/AgentTaskOverview.vue';

describe('AgentTaskOverview.vue', () => {
  it('zeigt Aufgaben pro Agent', async () => {
    const fetchMock = vi.fn(() => Promise.resolve({
      ok: true,
      json: () => Promise.resolve({
        tasks: [
          { task: 'a', agent: 'A' },
          { task: 'b', agent: 'B' },
          { task: 'c' }
        ]
      })
    }));
    const originalFetch = global.fetch;
    global.fetch = fetchMock;

    const wrapper = mount(AgentTaskOverview);
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledWith('/config');
    expect(wrapper.text()).toContain('A');
    expect(wrapper.text()).toContain('a');
    expect(wrapper.text()).toContain('auto');
    expect(wrapper.text()).toContain('c');

    wrapper.unmount();
    global.fetch = originalFetch;
  });
});
