import { mount, flushPromises } from '@vue/test-utils';
import { describe, it, expect, vi } from 'vitest';
import Models from '../src/components/Models.vue';

describe('Models.vue', () => {
  it('can add and remove models', async () => {
    const mockConfig = { models: ['m1'] };
    const fetchMock = vi.fn((url, opts) => {
      if (!opts) {
        return Promise.resolve({ json: () => Promise.resolve(mockConfig) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    });
    const originalFetch = global.fetch;
    global.fetch = fetchMock;

    const wrapper = mount(Models);
    await flushPromises();

    expect(wrapper.text()).toContain('m1');
    await wrapper.get('[data-test="new-name"]').setValue('m2');
    await wrapper.get('[data-test="add"]').trigger('click');
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const addBody = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(addBody).toEqual({ models: ['m1', 'm2'] });
    expect(wrapper.text()).toContain('m2');
    await wrapper.find('[data-test="delete"]').trigger('click');
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const deleteBody = JSON.parse(fetchMock.mock.calls[2][1].body);
    expect(deleteBody).toEqual({ models: ['m2'] });
    expect(wrapper.text()).not.toContain('m1');

    global.fetch = originalFetch;
  });
});
