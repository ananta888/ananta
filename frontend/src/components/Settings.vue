<template>
  <div>
    <h2>Einstellungen</h2>
    <label>Aktiver Agent:
      <select v-model="activeAgent">
        <option v-for="name in agentOptions" :key="name" :value="name">{{ name }}</option>
      </select>
    </label>
    <button @click="save">Save</button>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';

const activeAgent = ref('');
const agentOptions = ref([]);

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

onMounted(fetchSettings);
</script>

<style scoped>
label {
  margin-right: 10px;
}
</style>

