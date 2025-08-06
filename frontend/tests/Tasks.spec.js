import { mount, flushPromises } from '@vue/test-utils';
import { describe, it, expect, vi } from 'vitest';
import Tasks from '../src/components/Tasks.vue';

describe('Tasks.vue', () => {
  it('adds a task and persists it', async () => {
    const mockConfig = { tasks: [] };
    const fetchMock = vi.fn(async (url, opts) => {
      if (!opts) {
        return Promise.resolve({ json: () => Promise.resolve(mockConfig) });
      }
      const body = Object.fromEntries(opts.body.entries());
      if (body.add_task) {
        mockConfig.tasks.push({
          task: body.task_text,
          agent: body.task_agent,
          template: body.task_template
        });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    });
    const originalFetch = global.fetch;
    global.fetch = fetchMock;

    const wrapper = mount(Tasks);
    await flushPromises();

    await wrapper.get('.task-form input[placeholder="Task"]').setValue('t2');
    await wrapper.get('.task-form input[placeholder="Agent (optional)"]').setValue('Bob');
    await wrapper.get('.task-form input[placeholder="Template (optional)"]').setValue('tpl1');
    await wrapper.get('.task-form button').trigger('click');
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(3);
    const bodyEntries = Object.fromEntries(fetchMock.mock.calls[1][1].body.entries());
    expect(bodyEntries).toMatchObject({
      add_task: '1',
      task_text: 't2',
      task_agent: 'Bob',
      task_template: 'tpl1'
    });
    expect(wrapper.text()).toContain('t2');

    global.fetch = originalFetch;
  });
});
