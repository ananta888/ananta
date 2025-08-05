<template>
  <section>
    <h2>Pipeline</h2>
    <ul v-if="config">
      <li v-for="(name, idx) in config.pipeline_order" :key="name">
        {{ name }}
        <button @click="moveAgent(name, 'up')" :disabled="idx === 0">↑</button>
        <button @click="moveAgent(name, 'down')" :disabled="idx === config.pipeline_order.length - 1">↓</button>
      </li>
    </ul>
    <div class="log-section">
      <h2>Logs</h2>
      <button @click="loadControllerLog">Load Controller Log</button>
      <pre v-if="controllerLog">{{ controllerLog }}</pre>
    </div>
    <div class="control-section">
      <h2>Control</h2>
      <button @click="stop">Stop</button>
      <button @click="restart">Restart</button>
      <a :href="base + '/export'" target="_blank">Export logs</a>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted } from 'vue';

const base = '';
const config = ref(null);
const controllerLog = ref('');

async function loadConfig() {
  const res = await fetch(base + '/config');
  config.value = await res.json();
}

async function moveAgent(name, direction) {
  const form = new FormData();
  form.append('move_agent', name);
  form.append('direction', direction);
  await fetch(base + '/', { method: 'POST', body: form });
  await loadConfig();
}

async function loadControllerLog() {
  const res = await fetch(base + '/controller/status');
  const data = await res.json();
  controllerLog.value = Array.isArray(data) ? data.join('\n') : JSON.stringify(data);
}

async function stop() {
  await fetch(base + '/stop', { method: 'POST' });
}

async function restart() {
  await fetch(base + '/restart', { method: 'POST' });
}

onMounted(loadConfig);
</script>

<style scoped>
li {
  margin-bottom: 5px;
}
.log-section,
.control-section {
  margin-top: 20px;
}
</style>
