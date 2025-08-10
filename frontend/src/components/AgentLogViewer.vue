<template>
  <div class="agent-log-viewer">
    <h2>Agent Log</h2>
    <div class="controls">
      <label>
        Agent:
        <select v-model="selectedAgent" aria-label="Agent auswählen">
          <option v-for="name in agentOptions" :key="name" :value="name">{{ name }}</option>
        </select>
      </label>
      <label>
        Level:
        <select v-model="levelFilter" aria-label="Log-Level filtern">
          <option value="">Alle</option>
          <option value="DEBUG">DEBUG</option>
          <option value="INFO">INFO</option>
          <option value="WARN">WARN</option>
          <option value="ERROR">ERROR</option>
        </select>
      </label>
      <label>
        Limit:
        <input type="number" min="1" max="1000" v-model.number="limit" aria-label="Anzahl Einträge" />
      </label>
      <label>
        Seit:
        <input type="datetime-local" v-model="since" aria-label="Seit Zeitpunkt" />
      </label>
      <button @click="fetchLogs" aria-label="Logs neu laden">Aktualisieren</button>
      <button @click="clearLog" data-test="clear-log">Log löschen</button>
    </div>
    <div class="log-container">
      <div v-if="loading">Lade Logs...</div>
      <div v-else-if="error">{{ error }}</div>
      <div v-else>
        <p><strong>Aktueller Task:</strong> {{ taskInfo.current || '-' }}</p>
        <p><strong>Ausstehende Tasks:</strong></p>
        <ul>
          <li v-for="(t, idx) in taskInfo.pending" :key="idx">{{ t.task }}</li>
          <li v-if="taskInfo.pending.length === 0">Keine</li>
        </ul>
        <ul>
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
      detail: null,
      taskInfo: { current: '', pending: [] },
      levelFilter: '',
      limit: 100,
      since: ''
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
    async fetchLogs() {
      this.loading = true;
      this.error = '';
      try {
        const params = new URLSearchParams();
        if (this.limit) params.set('limit', String(this.limit));
        if (this.levelFilter) params.set('level', this.levelFilter);
        if (this.since) {
          try {
            const d = new Date(this.since);
            if (!isNaN(d.getTime())) params.set('since', d.toISOString());
          } catch (_) {}
        }
        const url = `/agent/${encodeURIComponent(this.selectedAgent)}/log` + (params.toString() ? `?${params.toString()}` : '');
        const res = await fetch(url);
        if (!res.ok) {
          const textErr = typeof res.text === 'function' ? await res.text() : '';
          throw new Error(textErr);
        }
        const contentType = res.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
          let data = await res.json();
          if (!Array.isArray(data)) data = [];
          // Optional client-side filtering fallback
          let items = data;
          if (this.levelFilter) items = items.filter(x => (x.level || '').toUpperCase() === this.levelFilter);
          this.logs = items.map(x => ({ raw: x.message, timestamp: '', level: x.level || '', message: x.message || '' }));
        } else {
          // Fallback: plain text lines
          const text = await res.text();
          this.logs = text.split(/\r?\n/).filter(Boolean).map(line => {
            const m = line.match(/^(\S+\s+\S+)\s+(\w+)\s+(.*)$/);
            if (m) return { timestamp: m[1], level: m[2], message: m[3], raw: line };
            return { raw: line, timestamp: '', level: '', message: line };
          });
        }
      } catch (e) {
        console.error('Fehler beim Abrufen der Logs: ', e);
        this.error = 'Fehler beim Abrufen der Logs';
        this.logs = [];
      } finally {
        this.loading = false;
      }
    },
    async fetchTaskInfo() {
      try {
        const res = await fetch(`/agent/${encodeURIComponent(this.selectedAgent)}/tasks`);
        if (res.ok === false) {
          const textErr = typeof res.text === 'function' ? await res.text() : '';
          throw new Error(textErr);
        }
        const data = await res.json();
        this.taskInfo.current = data.current_task || '';
        this.taskInfo.pending = data.tasks || [];
      } catch (e) {
        console.error('Fehler beim Abrufen der Tasks: ', e);
        this.taskInfo = { current: '', pending: [] };
      }
    },
    async clearLog() {
      try {
        await fetch(`/agent/${encodeURIComponent(this.selectedAgent)}/log`, { method: 'DELETE' });
        this.logs = [];
      } catch (e) {
        console.error('Fehler beim Löschen der Logs:', e);
      }
    }
  },
  async mounted() {
    await this.fetchAgents();
    await Promise.all([this.fetchLogs(), this.fetchTaskInfo()]);
    this.pollInterval = setInterval(() => {
      this.fetchLogs();
      this.fetchTaskInfo();
    }, 5000);
  },
  beforeUnmount() {
    clearInterval(this.pollInterval);
  },
  watch: {
    selectedAgent() {
      this.fetchLogs();
      this.fetchTaskInfo();
    },
    levelFilter() { this.fetchLogs(); },
    limit() { this.fetchLogs(); },
    since() { /* wait for explicit refresh unless user changes */ }
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

