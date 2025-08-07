<template>
  <div>
    <h2>Einstellungen</h2>
    <p v-if="error" class="error">{{ error }}</p>
    <label>Aktiver Agent:
      <select v-model="activeAgent">
        <option v-for="name in agentOptions" :key="name" :value="name">{{ name }}</option>
      </select>
    </label>
    <button @click="save" data-test="save">Save</button>

    <div class="log-section">
      <h3>Logs</h3>
      <button @click="loadControllerLog" data-test="load-log">Load Controller Log</button>
      <button @click="clearControllerLog" data-test="clear-log">Clear Controller Log</button>
      <pre v-if="controllerLog">{{ controllerLog }}</pre>
    </div>

    <div class="agent-config" v-if="Object.keys(agentConfig).length">
      <h3>Agent-Konfiguration</h3>
      <pre>{{ agentConfig }}</pre>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';

const activeAgent = ref('');
const agentOptions = ref([]);
const controllerLog = ref('');
const error = ref('');
const agentConfig = ref({});

const fetchSettings = async () => {
  try {
    const response = await fetch('/config');
    if (response.ok === false) {
      const text = typeof response.text === 'function' ? await response.text() : '';
      throw new Error(text);
    }
    const cfg = await response.json();
    activeAgent.value = cfg.active_agent || '';
    agentOptions.value = Object.keys(cfg.agents || {});
    error.value = '';

    try {
      const agentResp = await fetch('/agent/config');
      if (agentResp.ok) {
        agentConfig.value = await agentResp.json();
      }
    } catch (e) {
      console.error('Failed to load agent config:', e);
    }
  } catch (err) {
    console.error('Failed to load settings:', err);
    error.value = 'Fehler beim Laden der Konfiguration';
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

const clearControllerLog = async () => {
  try {
    await fetch('/controller/status', { method: 'DELETE' });
    controllerLog.value = '';
  } catch (err) {
    console.error('Failed to clear controller log:', err);
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
.error {
  color: red;
}
</style>

