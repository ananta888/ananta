import { mount, flushPromises } from '@vue/test-utils';
import { describe, it, expect, vi } from 'vitest';
import Templates from '../src/components/Templates.vue';

describe('Templates.vue', () => {
  it('adds and saves templates', async () => {
    const mockConfig = { prompt_templates: { tpl1: 'one' } };
    const fetchMock = vi.fn((url, opts) => {
      if (!opts) {
        return Promise.resolve({ json: () => Promise.resolve(mockConfig) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    });
    const originalFetch = global.fetch;
    global.fetch = fetchMock;

    const wrapper = mount(Templates);
    await flushPromises();

    await wrapper.get('input[placeholder="name"]').setValue('tpl2');
    await wrapper.get('textarea[placeholder="template"]').setValue('two');
    await wrapper.get('.template-form button').trigger('click');
    const saveButton = wrapper.findAll('button').find(b => b.text() === 'Save Templates');
    await saveButton.trigger('click');
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(3);
    const bodyEntries = Object.fromEntries(fetchMock.mock.calls[1][1].body.entries());
    const templates = JSON.parse(bodyEntries.prompt_templates);
    expect(templates).toHaveProperty('tpl2', 'two');

    global.fetch = originalFetch;
  });
});
