<template>
  <section>
    <h2>Agent Task Overview</h2>
    <p v-if="error" class="error">{{ error }}</p>
    <div v-if="Object.keys(grouped).length">
      <div v-for="(tasks, agent) in grouped" :key="agent" class="agent-tasks">
        <h3>{{ agent }}</h3>
        <ul>
          <li v-for="(t, idx) in tasks" :key="idx">{{ t.task }}</li>
          <li v-if="tasks.length === 0">Keine Aufgaben</li>
        </ul>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted } from 'vue';

const grouped = ref({});
const error = ref('');

async function loadConfig() {
  try {
    const res = await fetch('/config');
    if (!res.ok) throw new Error();
    const cfg = await res.json();
    const g = {};
    (cfg.tasks || []).forEach(t => {
      const agent = t.agent || 'auto';
      if (!g[agent]) g[agent] = [];
      g[agent].push(t);
    });
    grouped.value = g;
    error.value = '';
  } catch (e) {
    error.value = 'Fehler beim Laden der Konfiguration';
  }
}

onMounted(loadConfig);
</script>

<style scoped>
.agent-tasks {
  margin-bottom: 1rem;
}
.error {
  color: red;
}
</style>
