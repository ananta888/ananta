<template>
  <section>
    <h2>Tasks</h2>
    <p v-if="error" class="error">{{ error }}</p>
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
</template>

<script setup>
import { ref, onMounted } from 'vue';

const base = '';
const config = ref(null);
const taskText = ref('');
const taskAgent = ref('');
const taskTemplate = ref('');
const editingIndex = ref(-1);

const error = ref('');

async function loadConfig() {
  try {
    const res = await fetch(base + '/config');
    if (res.ok === false) {
      const text = typeof res.text === 'function' ? await res.text() : '';
      throw new Error(text);
    }
    config.value = await res.json();
    error.value = '';
  } catch (e) {
    error.value = 'Fehler beim Laden der Konfiguration';
  }
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
  const task = taskText.value.trim()
  if (!task) {
    error.value = 'Task darf nicht leer sein'
    return
  }
  const payload = { task }
  const agent = taskAgent.value.trim()
  const template = taskTemplate.value.trim()
  if (agent) payload.agent = agent
  if (template) payload.template = template
  try {
    const res = await fetch('/agent/add_task', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    if (!res.ok) {
      const text = typeof res.text === 'function' ? await res.text() : ''
      throw new Error(text)
    }
    await loadConfig()
    taskText.value = ''
    taskAgent.value = ''
    taskTemplate.value = ''
    error.value = ''
  } catch (e) {
    error.value = 'Fehler beim Hinzufügen der Aufgabe'
  }
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

onMounted(loadConfig);
</script>

<style scoped>
.task {
  border: 1px solid #ddd;
  padding: 10px;
  margin-bottom: 10px;
}
.error {
  color: red;
}
</style>
