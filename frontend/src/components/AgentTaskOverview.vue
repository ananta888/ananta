<template>
  <section>
    <header class="row" style="justify-content: space-between;">
      <h2>Agent-Aufgaben</h2>
      <div class="row">
        <button class="primary" @click="reload" :disabled="loading" data-testid="reload-agent-tasks">
          {{ loading ? 'Laden…' : 'Aktualisieren' }}
        </button>
      </div>
    </header>

    <p v-if="error" class="error" data-testid="error-agent-tasks">{{ error }}</p>
    <p v-else-if="loading" class="muted">Lade Konfiguration…</p>

    <div v-else>
      <p v-if="!agentNames.length" class="muted" data-testid="empty-agent-tasks">Keine Aufgaben vorhanden.</p>
      <div v-else class="grid">
        <div
          v-for="agent in agentNames"
          :key="agent"
          class="card agent-tasks"
          :data-testid="'agent-card-' + agent.toLowerCase().replace(/\s+/g, '-')"
        >
          <h3 class="row" style="justify-content: space-between;">
            <span>{{ agent }}</span>
            <small class="muted">{{ (grouped[agent] || []).length }} Aufgaben</small>
          </h3>
          <ul>
            <li v-for="(t, idx) in grouped[agent]" :key="idx">
              {{ displayTask(t) }}
            </li>
            <li v-if="(grouped[agent] || []).length === 0" class="muted">Keine Aufgaben</li>
          </ul>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue';

const grouped = ref({});
const loading = ref(false);
const error = ref('');

function displayTask(t) {
  if (!t) return ''
  const parts = []
  if (t.task) parts.push(t.task)
  if (t.template) parts.push(`(Template: ${t.template})`)
  return parts.join(' ')
}

async function loadConfig() {
  loading.value = true;
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
    // Sort keys and tasks for stable order
    Object.keys(g).forEach(k => {
      g[k].sort((a, b) => String(a.task || '').localeCompare(String(b.task || '')));
    })
    grouped.value = g;
    error.value = '';
  } catch (e) {
    error.value = 'Fehler beim Laden der Konfiguration';
  } finally {
    loading.value = false;
  }
}

const agentNames = computed(() => Object.keys(grouped.value).sort((a, b) => a.localeCompare(b)));

function reload() {
  loadConfig();
}

onMounted(loadConfig);
</script>

<style scoped>
.agent-tasks { margin-bottom: 1rem; }
.error { color: red; }
ul { margin: 0.25rem 0 0; padding-left: 1rem; }
</style>
