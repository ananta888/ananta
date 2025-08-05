import { mount, flushPromises } from '@vue/test-utils';
import { describe, it, expect, vi } from 'vitest';
import Agents from '../src/components/Agents.vue';

describe('Agents.vue', () => {
  it('loads agents and enters edit mode', async () => {
    const mockConfig = {
      agents: {
        Bob: {
          model: { name: 'm1', type: '', reasoning: '', sources: [] },
          models: ['m1']
        }
      },
      models: ['m1', 'm2'],
      prompt_templates: { tpl1: 'one', tpl2: 'two' }
    };
    const fetchMock = vi.fn(() => Promise.resolve({ json: () => Promise.resolve(mockConfig) }));
    const originalFetch = global.fetch;
    global.fetch = fetchMock;

    const wrapper = mount(Agents);
    await flushPromises();

    expect(fetchMock).toHaveBeenCalled();
    expect(wrapper.text()).toContain('Bob');

    const newTemplateSelect = wrapper.find('[data-test="new-template"]');
    expect(newTemplateSelect.exists()).toBe(true);
    expect(newTemplateSelect.findAll('option')).toHaveLength(3);

    await wrapper.find('[data-test="edit"]').trigger('click');
    expect(wrapper.find('select[multiple]').exists()).toBe(true);
    const editTemplateSelect = wrapper.find('[data-test="edit-template"]');
    expect(editTemplateSelect.exists()).toBe(true);
    expect(editTemplateSelect.findAll('option')).toHaveLength(3);

    global.fetch = originalFetch;
  });
});

