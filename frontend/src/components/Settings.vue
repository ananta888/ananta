<template>
  <div>
    <h2>Einstellungen</h2>
    <label>Aktiver Agent:
      <select v-model="activeAgent">
        <option v-for="name in agentOptions" :key="name" :value="name">{{ name }}</option>
      </select>
    </label>
    <button @click="save" data-test="save">Save</button>

    <div class="log-section">
      <h3>Logs</h3>
      <button @click="loadControllerLog" data-test="load-log">Load Controller Log</button>
      <pre v-if="controllerLog">{{ controllerLog }}</pre>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';

const activeAgent = ref('');
const agentOptions = ref([]);
const controllerLog = ref('');

const fetchSettings = async () => {
  try {
    const response = await fetch('/config');
    const cfg = await response.json();
    activeAgent.value = cfg.active_agent || '';
    agentOptions.value = Object.keys(cfg.agents || {});
  } catch (err) {
    console.error('Failed to load settings:', err);
  }
};

const save = async () => {
  try {
    await fetch('/config/active_agent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active_agent: activeAgent.value })
    });
  } catch (err) {
    console.error('Failed to save settings:', err);
  }
};

const loadControllerLog = async () => {
  try {
    const res = await fetch('/controller/status');
    const data = await res.json();
    controllerLog.value = Array.isArray(data) ? data.join('\n') : JSON.stringify(data);
  } catch (err) {
    console.error('Failed to load controller log:', err);
  }
};

onMounted(fetchSettings);
</script>

<style scoped>
label {
  margin-right: 10px;
}
.log-section {
  margin-top: 20px;
}
</style>

