import { mount, flushPromises } from '@vue/test-utils';
import { describe, it, expect, vi } from 'vitest';
import Tasks from '../src/components/Tasks.vue';

describe('Tasks.vue', () => {
    it('adds a task and persists it', async () => {
      const mockConfig = { tasks: [] };
      const fetchMock = vi.fn(async (url, opts) => {
        if (!opts) {
          return Promise.resolve({ json: () => Promise.resolve({ ...mockConfig, tasks: [...mockConfig.tasks] }) });
        }
        let body = {};
        if (opts.headers && opts.headers['Content-Type'] === 'application/json') {
          body = JSON.parse(opts.body);
        } else if (opts.body && typeof opts.body.entries === 'function') {
          body = Object.fromEntries(opts.body.entries());
        }
        if (body.task) {
          mockConfig.tasks.push({
            task: body.task,
            agent: body.agent,
            template: body.template
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
      const bodyEntries = JSON.parse(fetchMock.mock.calls[1][1].body);
      expect(bodyEntries).toMatchObject({
        task: 't2',
        agent: 'Bob',
        template: 'tpl1'
      });
      expect(wrapper.text()).toContain('t2');

      global.fetch = originalFetch;
    });

  it('removes a task when skipped', async () => {
    const mockConfig = { tasks: [{ task: 't1', agent: 'Alice' }] };
    const fetchMock = vi.fn(async (url, opts) => {
      if (!opts) {
        return Promise.resolve({ json: () => Promise.resolve({ ...mockConfig, tasks: [...mockConfig.tasks] }) });
      }
      const body = Object.fromEntries(opts.body.entries());
      if (body.task_action === 'skip') {
        mockConfig.tasks.splice(parseInt(body.task_idx, 10), 1);
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    });
    const originalFetch = global.fetch;
    global.fetch = fetchMock;

    const wrapper = mount(Tasks);
    await flushPromises();

    expect(wrapper.text()).toContain('t1');
    const skipBtn = wrapper.findAll('.task button').find(b => b.text() === 'Skip');
    await skipBtn.trigger('click');
    await flushPromises();

    const bodyEntries = Object.fromEntries(fetchMock.mock.calls[1][1].body.entries());
    expect(bodyEntries).toMatchObject({ task_action: 'skip', task_idx: '0' });
    expect(wrapper.text()).not.toContain('t1');

    global.fetch = originalFetch;
  });
});
