<template>
  <div class="agent-log-viewer">
    <h2>Agent Log</h2>
    <div class="controls">
      <label>
        Quelle:
        <select v-model="source" aria-label="Log-Quelle auswählen">
          <option v-for="s in sources" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>

      <template v-if="source === 'Agent'">
        <label>
          Agent:
          <select v-model="selectedAgent" aria-label="Agent auswählen">
            <option v-for="name in agentOptions" :key="name" :value="name">{{ name }}</option>
          </select>
        </label>
      </template>

      <template v-if="source === 'Datei'">
        <label>
          Datei:
          <select v-model="selectedFile" aria-label="Logdatei auswählen">
            <option v-for="f in files" :key="f.name" :value="f.name">{{ f.name }} ({{ f.size || 0 }} B)</option>
          </select>
        </label>
      </template>

      <label>
        Level:
        <select v-model="levelFilter" aria-label="Log-Level filtern">
          <option value="">Alle</option>
          <option value="TERMINAL">TERMINAL</option>
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
      <button v-if="source === 'Agent'" @click="clearLog" data-test="clear-log">Log löschen</button>
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
function buildRaw(entry, parsed) {
  try {
    const lvl = (entry.level || '').toUpperCase();
    if (lvl === 'TERMINAL' && parsed && typeof parsed === 'object') {
      const step = (parsed.step !== undefined) ? `#${parsed.step}` : '';
      const dir = parsed.direction === 'input' ? 'IN' : (parsed.direction === 'output' ? 'OUT' : '');
      if (parsed.direction === 'input') {
        return `[${step}] [${dir}] ${parsed.command || ''}`.trim();
      }
      if (parsed.direction === 'output') {
        const rc = (parsed.returncode !== undefined && parsed.returncode !== null) ? ` rc=${parsed.returncode}` : '';
        let out = typeof parsed.output === 'string' ? parsed.output : JSON.stringify(parsed.output || '');
        out = out.replace(/\s+/g, ' ').slice(0, 200);
        return `[${step}] [${dir}${rc}] ${out}`.trim();
      }
    }
  } catch (_) {}
  // fallback
  return `${entry.timestamp ? entry.timestamp + ' ' : ''}${entry.level ? entry.level + ' ' : ''}${entry.message || entry.raw || ''}`.trim();
}

export default {
  name: 'AgentLogViewer',
  data() {
    return {
      source: 'Agent',
      sources: ['Agent', 'Controller', 'Datei'],
      logs: [],
      files: [],
      selectedFile: '',
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
    async fetchFiles() {
      try {
        const res = await fetch('/logs/files');
        if (res.ok) {
          const data = await res.json();
          this.files = Array.isArray(data) ? data : [];
          if (!this.selectedFile && this.files.length > 0) {
            this.selectedFile = this.files[0].name;
          }
        }
      } catch (e) {
        console.error('Fehler beim Laden der Logdateien:', e);
      }
    },
    async fetchLogs() {
      this.loading = true;
      this.error = '';
      try {
        const params = new URLSearchParams();
        if (this.limit) params.set('limit', String(this.limit));
        if (this.source === 'Agent') {
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
            let items = data;
            if (this.levelFilter) items = items.filter(x => (x.level || '').toUpperCase() === this.levelFilter);
            this.logs = items.map(x => {
              let msg = x.message || '';
              let parsed = null;
              if ((x.level || '').toUpperCase() === 'TERMINAL') {
                try {
                  parsed = JSON.parse(msg);
                } catch (_) {
                  parsed = null;
                }
              }
              const raw = buildRaw(x, parsed);
              return { raw, timestamp: x.timestamp || '', level: x.level || '', message: msg, parsed };
            });
          } else {
            const text = await res.text();
            this.logs = text.split(/\r?\n/).filter(Boolean).map(line => {
              const m = line.match(/^(\S+\s+\S+)\s+(\w+)\s+(.*)$/);
              if (m) return { timestamp: m[1], level: m[2], message: m[3], raw: line };
              return { raw: line, timestamp: '', level: '', message: line };
            });
          }
        } else if (this.source === 'Controller') {
          const url = '/controller/logs' + (params.toString() ? `?${params.toString()}` : '');
          const res = await fetch(url);
          if (!res.ok) throw new Error('Fehler beim Laden der Controller-Logs');
          let data = await res.json();
          if (!Array.isArray(data)) data = [];
          this.logs = data.map(x => {
            const msg = x.summary || x.approved || x.received || '';
            const raw = `${x.timestamp || ''} ${msg}`.trim();
            return { raw, timestamp: x.timestamp || '', level: '', message: msg };
          });
        } else if (this.source === 'Datei') {
          if (!this.selectedFile) {
            await this.fetchFiles();
          }
          if (!this.selectedFile) {
            this.logs = [];
            return;
          }
          const url = `/logs/file/${encodeURIComponent(this.selectedFile)}` + (params.toString() ? `?${params.toString()}` : '');
          const res = await fetch(url);
          if (!res.ok) throw new Error('Fehler beim Laden der Logdatei');
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
      if (this.source !== 'Agent') {
        this.taskInfo = { current: '', pending: [] };
        return;
      }
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
    if (this.source === 'Datei') {
      await this.fetchFiles();
    }
    await this.fetchLogs();
    await this.fetchTaskInfo();
    this.pollInterval = setInterval(() => {
      this.fetchLogs();
      if (this.source === 'Agent') {
        this.fetchTaskInfo();
      }
    }, 5000);
  },
  beforeUnmount() {
    clearInterval(this.pollInterval);
  },
  watch: {
    selectedAgent() {
      this.fetchLogs();
      if (this.source === 'Agent') this.fetchTaskInfo();
    },
    selectedFile() {
      if (this.source === 'Datei') this.fetchLogs();
    },
    source() {
      if (this.source === 'Datei') {
        this.fetchFiles().then(() => this.fetchLogs());
      } else {
        this.fetchLogs();
      }
      if (this.source !== 'Agent') {
        this.taskInfo = { current: '', pending: [] };
      } else {
        this.fetchTaskInfo();
      }
    },
    levelFilter() { if (this.source === 'Agent') this.fetchLogs(); },
    limit() { this.fetchLogs(); },
    since() { /* explicit refresh via button */ }
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

