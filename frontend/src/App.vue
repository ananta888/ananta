<template>
  <div class="container">
    <h1>Agent Controller Dashboard</h1>
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
const logs = ref({});

async function loadConfig() {
  const res = await fetch(base + '/config');
  config.value = await res.json();
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

onMounted(loadConfig);
</script>

<style>
.container {
  font-family: Arial, sans-serif;
  margin: 20px;
}
.agent {
  border: 1px solid #ddd;
  padding: 10px;
  margin-bottom: 10px;
}
</style>
