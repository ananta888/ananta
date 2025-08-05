import { mount, flushPromises } from '@vue/test-utils';
import { describe, it, expect, vi } from 'vitest';
import Endpoints from '../src/components/Endpoints.vue';

describe('Endpoints.vue', () => {
  it('can add and remove endpoints', async () => {
    const mockConfig = {
      api_endpoints: [{ type: 't1', url: 'u1', models: ['m1'] }],
    };
    const fetchMock = vi.fn((url, opts) => {
      if (!opts) {
        return Promise.resolve({ json: () => Promise.resolve(mockConfig) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    });
    const originalFetch = global.fetch;
    global.fetch = fetchMock;

    const wrapper = mount(Endpoints);
    await flushPromises();

    expect(fetchMock).toHaveBeenCalled();
    expect(wrapper.text()).toContain('t1');
    expect(wrapper.text()).toContain('m1');

    await wrapper.get('[data-test="new-type"]').setValue('t2');
    await wrapper.get('[data-test="new-url"]').setValue('u2');
    await wrapper.get('[data-test="new-models"]').setValue('m2,m3');
    await wrapper.get('[data-test="add"]').trigger('click');
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const postBody = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(postBody.api_endpoints[1]).toEqual({ type: 't2', url: 'u2', models: ['m2', 'm3'] });
    expect(wrapper.text()).toContain('t2');
    expect(wrapper.text()).toContain('m2');

    await wrapper.findAll('[data-test="delete"]')[0].trigger('click');
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(wrapper.text()).not.toContain('t1');

    global.fetch = originalFetch;
  });
});
