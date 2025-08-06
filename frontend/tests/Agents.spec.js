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

  it('adds a new agent and saves configuration', async () => {
    const mockConfig = {
      agents: {},
      models: ['m1', 'm2'],
      prompt_templates: { tpl1: 'one' }
    };
    const fetchMock = vi.fn((url, opts) => {
      if (!opts) {
        return Promise.resolve({ json: () => Promise.resolve(mockConfig) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    });
    const originalFetch = global.fetch;
    global.fetch = fetchMock;

    const wrapper = mount(Agents);
    await flushPromises();

    await wrapper.get('[data-test="new-name"]').setValue('Alice');
    await wrapper.get('input[placeholder="model.name"]').setValue('modelA');
    await wrapper.get('input[placeholder="model.type"]').setValue('typeA');
    await wrapper.get('input[placeholder="model.reasoning"]').setValue('reasonA');
    await wrapper.get('input[placeholder="model.sources (comma separated)"]').setValue('s1,s2');
    await wrapper.get('select[data-test="new-models"]').setValue(['m1']);
    await wrapper.get('select[data-test="new-template"]').setValue('tpl1');
    await wrapper.get('input[placeholder="max_summary_length"]').setValue('10');
    await wrapper.get('input[placeholder="step_delay"]').setValue('5');
    const checkboxes = wrapper.findAll('input[type="checkbox"]');
    await checkboxes[0].setValue(true);
    await checkboxes[1].setValue(true);
    await checkboxes[2].setValue(true);
    await wrapper.get('input[placeholder="prompt"]').setValue('hi');
    await wrapper.get('input[placeholder="tasks (comma separated)"]').setValue('t1,t2');
    await wrapper.get('input[placeholder="purpose"]').setValue('testing');
    await wrapper.get('input[placeholder="preferred_hardware"]').setValue('GPU');
    await wrapper.get('[data-test="add"]').trigger('click');
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const postBody = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(postBody.agents).toHaveProperty('Alice');
    expect(postBody.agents.Alice).toEqual({
      model: {
        name: 'modelA',
        type: 'typeA',
        reasoning: 'reasonA',
        sources: ['s1', 's2']
      },
      models: ['m1'],
      template: 'tpl1',
      max_summary_length: 10,
      step_delay: 5,
      auto_restart: true,
      allow_commands: true,
      controller_active: true,
      prompt: 'hi',
      tasks: ['t1', 't2'],
      purpose: 'testing',
      preferred_hardware: 'GPU'
    });

    global.fetch = originalFetch;
  });
});

