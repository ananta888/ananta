<template>
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

onMounted(loadConfig);
</script>

<style scoped>
.agent {
  border: 1px solid #ddd;
  padding: 10px;
  margin-bottom: 10px;
}
</style>
