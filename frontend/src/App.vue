<template>
  <div class="container">
    <h1>Agent Controller Dashboard</h1>

    <section>
      <h2>Pipeline</h2>
      <ul v-if="config">
        <li v-for="(name, idx) in config.pipeline_order" :key="name">
          {{ name }}
          <button @click="moveAgent(name, 'up')" :disabled="idx === 0">↑</button>
          <button @click="moveAgent(name, 'down')" :disabled="idx === config.pipeline_order.length - 1">↓</button>
        </li>
      </ul>
    </section>

    <section>
      <h2>Agents</h2>
      <div v-if="config">
        <div v-for="(agent, name) in config.agents" :key="name" class="agent">
          <h3>{{ name }}</h3>
          <p>Model: {{ agent.model }} - Provider: {{ agent.provider }}</p>
          <button @click="toggle(name)">
            {{ agent.controller_active ? 'Deactivate' : 'Activate' }}
          </button>
          <button @click="loadLog(name)">Log</button>
          <pre v-if="logs[name]">{{ logs[name] }}</pre>
        </div>
      </div>
    </section>

    <section>
      <h2>Templates</h2>
      <div v-for="(text, name) in templates" :key="name" class="template">
        <label>{{ name }}</label>
        <textarea v-model="templates[name]"></textarea>
        <button @click="deleteTemplate(name)">Delete</button>
      </div>
      <div class="template-form">
        <input v-model="newTemplate.name" placeholder="name" />
        <textarea v-model="newTemplate.text" placeholder="template"></textarea>
        <button @click="addTemplate">Add</button>
      </div>
      <button @click="saveTemplates">Save Templates</button>
    </section>

    <section>
      <h2>Tasks</h2>
      <div v-if="config">
        <div v-for="(t, idx) in config.tasks" :key="idx" class="task">
          <div v-if="editingIndex !== idx">
            <span>
              {{ t.task }} ({{ t.agent || 'auto' }}<span v-if="t.template">, template: {{ t.template }}</span>)
            </span>
            <button @click="startTask(idx)">Start</button>
            <button @click="taskAction(idx, 'move_up')" :disabled="idx===0">↑</button>
            <button @click="taskAction(idx, 'move_down')" :disabled="idx===config.tasks.length-1">↓</button>
            <button @click="taskAction(idx, 'skip')">Skip</button>
            <button @click="editTask(idx)">Edit</button>
          </div>
          <div v-else>
            <input v-model="taskText" placeholder="Task" />
            <input v-model="taskAgent" placeholder="Agent (optional)" />
            <input v-model="taskTemplate" placeholder="Template (optional)" />
            <button @click="saveTask(idx)">Save</button>
            <button @click="cancelEdit">Cancel</button>
          </div>
        </div>
        <div class="task-form">
          <h3>Add Task</h3>
          <input v-model="taskText" placeholder="Task" />
          <input v-model="taskAgent" placeholder="Agent (optional)" />
          <input v-model="taskTemplate" placeholder="Template (optional)" />
          <button @click="addTask">Add</button>
        </div>
      </div>
    </section>

    <section>
      <h2>Logs</h2>
      <button @click="loadControllerLog">Load Controller Log</button>
      <pre v-if="controllerLog">{{ controllerLog }}</pre>
    </section>

    <section>
      <h2>Control</h2>
      <button @click="stop">Stop</button>
      <button @click="restart">Restart</button>
      <a :href="base + '/export'" target="_blank">Export logs</a>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';

const base = '';
const config = ref(null);
const templates = ref({});
const logs = ref({});
const controllerLog = ref('');
const newTemplate = ref({ name: '', text: '' });
const taskText = ref('');
const taskAgent = ref('');
const taskTemplate = ref('');
const editingIndex = ref(-1);

async function loadConfig() {
  const res = await fetch(base + '/config');
  const data = await res.json();
  config.value = data;
  templates.value = JSON.parse(JSON.stringify(data.prompt_templates || {}));
}

async function toggle(name) {
  const res = await fetch(base + `/agent/${encodeURIComponent(name)}/toggle_active`, { method: 'POST' });
  const data = await res.json();
  if (config.value && config.value.agents[name]) {
    config.value.agents[name].controller_active = data.controller_active;
  }
}

async function loadLog(name) {
  const res = await fetch(base + `/agent/${encodeURIComponent(name)}/log`);
  logs.value[name] = await res.text();
}

async function stop() {
  await fetch(base + '/stop', { method: 'POST' });
}

async function restart() {
  await fetch(base + '/restart', { method: 'POST' });
}

async function moveAgent(name, direction) {
  const form = new FormData();
  form.append('move_agent', name);
  form.append('direction', direction);
  await fetch(base + '/', { method: 'POST', body: form });
  await loadConfig();
}

function addTemplate() {
  if (newTemplate.value.name) {
    templates.value[newTemplate.value.name] = newTemplate.value.text;
    newTemplate.value = { name: '', text: '' };
  }
}

function deleteTemplate(name) {
  delete templates.value[name];
}

async function saveTemplates() {
  const form = new FormData();
  form.append('prompt_templates', JSON.stringify(templates.value));
  await fetch(base + '/', { method: 'POST', body: form });
  await loadConfig();
}

function editTask(idx) {
  editingIndex.value = idx;
  const t = config.value.tasks[idx];
  taskText.value = t.task;
  taskAgent.value = t.agent || '';
  taskTemplate.value = t.template || '';
}

function cancelEdit() {
  editingIndex.value = -1;
  taskText.value = '';
  taskAgent.value = '';
  taskTemplate.value = '';
}

async function saveTask(idx) {
  const form = new FormData();
  form.append('task_action', 'update');
  form.append('task_idx', idx);
  form.append('task_text', taskText.value);
  form.append('task_agent', taskAgent.value);
  form.append('task_template', taskTemplate.value);
  await fetch(base + '/', { method: 'POST', body: form });
  await loadConfig();
  cancelEdit();
}

async function addTask() {
  const form = new FormData();
  form.append('add_task', '1');
  form.append('task_text', taskText.value);
  form.append('task_agent', taskAgent.value);
  form.append('task_template', taskTemplate.value);
  await fetch(base + '/', { method: 'POST', body: form });
  await loadConfig();
  taskText.value = '';
  taskAgent.value = '';
  taskTemplate.value = '';
}

async function taskAction(idx, action) {
  const form = new FormData();
  form.append('task_action', action);
  form.append('task_idx', idx);
  await fetch(base + '/', { method: 'POST', body: form });
  await loadConfig();
}

async function startTask(idx) {
  await taskAction(idx, 'start');
}

async function loadControllerLog() {
  const res = await fetch(base + '/controller/status');
  const data = await res.json();
  controllerLog.value = Array.isArray(data) ? data.join('\n') : JSON.stringify(data);
}

onMounted(loadConfig);
</script>

<style>
.container {
  font-family: Arial, sans-serif;
  margin: 20px;
}
.agent,
.task,
.template {
  border: 1px solid #ddd;
  padding: 10px;
  margin-bottom: 10px;
}
textarea {
  width: 100%;
  min-height: 60px;
}
</style>

