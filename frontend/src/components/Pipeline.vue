<template>
  <section>
    <h2>Pipeline</h2>
    <p v-if="error" class="error">{{ error }}</p>
    <ul v-if="config">
      <li v-for="(name, idx) in config.pipeline_order" :key="name">
        <div class="agent-header">
          {{ name }}
          <button @click="moveAgent(name, 'up')" :disabled="idx === 0">↑</button>
          <button @click="moveAgent(name, 'down')" :disabled="idx === config.pipeline_order.length - 1">↓</button>
          <button @click="toggleAgent(name)">
            {{ config.agents[name]?.controller_active ? 'Deactivate' : 'Activate' }}
          </button>
          <button v-if="config.active_agent === name" @click="stop">Stop</button>
        </div>
        <div class="current-task">Aktueller Task: {{ config.agents[name]?.current_task || '-' }}</div>
        <pre>{{ agentLogs[name] }}</pre>
      </li>
    </ul>
  </section>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';

const base = '';
const config = ref(null);
const agentLogs = ref({});
const error = ref('');
let timer = null;

async function loadConfig() {
  try {
    const res = await fetch(base + '/config');
    if (!res.ok) throw new Error(await res.text());
    config.value = await res.json();
    await loadLogs();
    error.value = '';
  } catch (e) {
    error.value = 'Fehler beim Laden der Konfiguration';
  }
}

async function loadLogs() {
  if (!config.value) return;
  const logs = {};
  for (const name of config.value.pipeline_order) {
    try {
      const res = await fetch(`${base}/agent/${encodeURIComponent(name)}/log`);
      if (!res.ok) throw new Error(await res.text());
      logs[name] = await res.text();
    } catch (e) {
      logs[name] = 'Fehler beim Laden des Logs';
    }
  }
  agentLogs.value = logs;
}

async function moveAgent(name, direction) {
  const form = new FormData();
  form.append('move_agent', name);
  form.append('direction', direction);
  await fetch(base + '/', { method: 'POST', body: form });
  await loadConfig();
}

async function toggleAgent(name) {
  await fetch(`${base}/agent/${encodeURIComponent(name)}/toggle_active`, { method: 'POST' });
  await loadConfig();
}

async function stop() {
  await fetch(base + '/stop', { method: 'POST' });
}

onMounted(() => {
  loadConfig();
  timer = setInterval(loadConfig, 5000);
});
onUnmounted(() => clearInterval(timer));
</script>

<style scoped>
li {
  margin-bottom: 20px;
}
.agent-header {
  display: flex;
  align-items: center;
  gap: 5px;
}
pre {
  background: #f4f4f4;
  padding: 10px;
  max-height: 200px;
  overflow-y: auto;
}
.error {
  color: red;
}
.current-task {
  font-style: italic;
  margin-bottom: 5px;
}
</style>
