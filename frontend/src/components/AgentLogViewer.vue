<template>
  <div class="agent-log-viewer">
    <h2>Agent Log</h2>
    <div class="controls">
      <label>
        Agent:
        <select v-model="selectedAgent">
          <option v-for="name in agentOptions" :key="name" :value="name">{{ name }}</option>
        </select>
      </label>
    </div>
    <div class="log-container">
      <div v-if="loading">Lade Logs...</div>
      <div v-else-if="error">{{ error }}</div>
      <ul v-else>
        <li
          v-for="(entry, idx) in logs"
          :key="idx"
          class="log-entry"
          @click="detail = entry"
        >
          {{ entry.raw }}
        </li>
      </ul>
    </div>
    <div v-if="detail" class="log-detail">
      <h3>Details</h3>
      <p><strong>Zeit:</strong> {{ detail.timestamp }}</p>
      <p><strong>Level:</strong> {{ detail.level }}</p>
      <pre>{{ detail.message }}</pre>
      <button @click="detail = null">Schließen</button>
    </div>
  </div>
</template>

<script>
export default {
  name: 'AgentLogViewer',
  data() {
    return {
      logs: [],
      agentOptions: [],
      selectedAgent: 'default',
      pollInterval: null,
      loading: false,
      error: '',
      detail: null
    };
  },
  methods: {
    async fetchAgents() {
      try {
        const res = await fetch('/config');
        if (res.ok === false) {
          const text = typeof res.text === 'function' ? await res.text() : '';
          throw new Error(text);
        }
        const cfg = await res.json();
        this.agentOptions = Object.keys(cfg.agents || {});
        if (!this.agentOptions.includes(this.selectedAgent)) {
          this.selectedAgent = this.agentOptions[0] || 'default';
        }
      } catch (e) {
        console.error('Fehler beim Laden der Agenten:', e);
        this.error = 'Fehler beim Laden der Agenten';
      }
    },
    parseLine(line) {
      const m = line.match(/^(\S+\s+\S+)\s+(\w+)\s+(.*)$/);
      if (m) {
        return { timestamp: m[1], level: m[2], message: m[3], raw: line };
      }
      return { raw: line, timestamp: '', level: '', message: line };
    },
    async fetchLogs() {
      this.loading = true;
      this.error = '';
      try {
        const res = await fetch(`/agent/${encodeURIComponent(this.selectedAgent)}/log`);
        if (res.ok === false) {
          const textErr = typeof res.text === 'function' ? await res.text() : '';
          throw new Error(textErr);
        }
        const text = await res.text();
        this.logs = text
          .split(/\r?\n/)
          .filter(Boolean)
          .map(this.parseLine);
      } catch (e) {
        console.error('Fehler beim Abrufen der Logs: ', e);
        this.error = 'Fehler beim Abrufen der Logs';
        this.logs = [];
      } finally {
        this.loading = false;
      }
    }
  },
  async mounted() {
    await this.fetchAgents();
    await this.fetchLogs();
    this.pollInterval = setInterval(this.fetchLogs, 5000);
  },
  beforeUnmount() {
    clearInterval(this.pollInterval);
  },
  watch: {
    selectedAgent() {
      this.fetchLogs();
    }
  }
};
</script>

<style scoped>
.agent-log-viewer {
  background-color: #f9f9f9;
  border: 1px solid #ddd;
  padding: 1rem;
  max-height: 400px;
  overflow: hidden;
}
.log-container {
  max-height: 250px;
  overflow-y: auto;
  margin-top: 0.5rem;
}
.log-entry {
  cursor: pointer;
  white-space: pre-wrap;
}
.log-entry:hover {
  background: #eee;
}
.log-detail {
  margin-top: 1rem;
  border-top: 1px solid #ccc;
  padding-top: 0.5rem;
}
</style>

