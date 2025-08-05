<template>
  <section>
    <h2>Pipeline</h2>
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
        <pre>{{ agentLogs[name] }}</pre>
      </li>
    </ul>
  </section>
</template>

<script setup>
import { ref, onMounted } from 'vue';

const base = '';
const config = ref(null);
const agentLogs = ref({});

async function loadConfig() {
  const res = await fetch(base + '/config');
  config.value = await res.json();
  await loadLogs();
}

async function loadLogs() {
  if (!config.value) return;
  const logs = {};
  for (const name of config.value.pipeline_order) {
    const res = await fetch(`${base}/agent/${encodeURIComponent(name)}/log`);
    logs[name] = await res.text();
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

onMounted(loadConfig);
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
</style>
