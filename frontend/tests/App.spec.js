import { mount } from '@vue/test-utils';
import { describe, it, expect } from 'vitest';
import App from '../src/App.vue';

const stub = name => ({ template: `<div class="${name}">${name}</div>` });

describe('App.vue', () => {
  it('switches tabs when clicked', async () => {
    const wrapper = mount(App, {
      global: {
        stubs: {
          Pipeline: stub('pipeline'),
          Agents: stub('agents'),
          Tasks: stub('tasks'),
          Templates: stub('templates'),
          Endpoints: stub('endpoints'),
          Models: stub('models'),
          Settings: stub('settings')
        }
      }
    });
    expect(wrapper.find('.pipeline').exists()).toBe(true);
    await wrapper.findAll('button')[1].trigger('click');
    expect(wrapper.find('.agents').exists()).toBe(true);
    await wrapper.findAll('button')[4].trigger('click');
    expect(wrapper.find('.endpoints').exists()).toBe(true);
    await wrapper.findAll('button')[5].trigger('click');
    expect(wrapper.find('.models').exists()).toBe(true);
    await wrapper.findAll('button')[6].trigger('click');
    expect(wrapper.find('.settings').exists()).toBe(true);
  });
});
