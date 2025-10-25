<template>
  <div>
    <h2>Agenten</h2>
    <div v-if="loadError" class="banner error" role="alert" aria-live="assertive">
      <span>{{ loadError }}</span>
      <button @click="fetchAgents" aria-label="Erneut laden">Retry</button>
    </div>


    <div class="controls">
      <label for="search-input">Suche</label>
      <input id="search-input" v-model="searchInput" placeholder="Name oder Template" data-test="search" />
      <label><input type="checkbox" v-model="onlyActive" /> Nur aktive</label>
    </div>

    <div v-if="isLoading" class="loading" aria-live="polite">Lade Agenten…</div>

    <table v-else>
      <caption class="sr-only">Agenten Tabelle</caption>
      <thead>
        <tr>
          <th scope="col" :aria-sort="ariaSort('name')">
            <button type="button" @click="setSort('name')" aria-label="Nach Name sortieren">Name {{ sortIcon('name') }}</button>
          </th>
          <th scope="col">model.name</th>
          <th scope="col">model.type</th>
          <th scope="col">model.reasoning</th>
          <th scope="col">model.sources</th>
          <th scope="col">models</th>
          <th scope="col" :aria-sort="ariaSort('template')">
            <button type="button" @click="setSort('template')" aria-label="Nach Template sortieren">template {{ sortIcon('template') }}</button>
          </th>
          <th scope="col">max_summary_length</th>
          <th scope="col">step_delay</th>
          <th scope="col" :aria-sort="ariaSort('active')">
            <button type="button" @click="setSort('active')" aria-label="Nach Aktiv sortieren">controller_active {{ sortIcon('active') }}</button>
          </th>
          <th scope="col">allow_commands</th>
          <th scope="col">prompt</th>
          <th scope="col">tasks</th>
          <th scope="col">purpose</th>
          <th scope="col">preferred_hardware</th>
          <th scope="col">Aktionen</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="([name, agent], idx) in pagedEntries" :key="name">
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.name" :aria-invalid="!!editErrors.name" :aria-describedby="editErrors.name ? 'edit-name-error' : undefined" ref="editFirstInput" />
              <div v-if="editErrors.name" id="edit-name-error" class="field-error">{{ editErrors.name }}</div>
            </div>
            <div v-else>{{ name }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.model.name" :aria-invalid="!!editErrors['model.name']" :aria-describedby="editErrors['model.name'] ? 'edit-model-name-error' : undefined" />
              <div v-if="editErrors['model.name']" id="edit-model-name-error" class="field-error">{{ editErrors['model.name'] }}</div>
            </div>
            <div v-else>{{ agent.model?.name }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.model.type" />
            </div>
            <div v-else>{{ agent.model?.type }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.model.reasoning" />
            </div>
            <div v-else>{{ agent.model?.reasoning }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.model_sources" />
            </div>
            <div v-else>{{ (agent.model?.sources || []).join(', ') }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <select v-model="editableAgent.models" multiple>
                <option v-for="m in modelOptions" :key="m" :value="m">{{ m }}</option>
              </select>
              <div v-if="editErrors.models" class="field-error">{{ editErrors.models }}</div>
            </div>
            <div v-else>{{ (agent.models || []).join(', ') }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <select v-model="editableAgent.template" data-test="edit-template">
                <option value=""></option>
                <option v-for="t in templateOptions" :key="t" :value="t">{{ t }}</option>
              </select>
              <div v-if="editErrors.template" class="field-error">{{ editErrors.template }}</div>
            </div>
            <div v-else>{{ agent.template }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input type="number" v-model.number="editableAgent.max_summary_length" />
              <div v-if="editErrors.max_summary_length" class="field-error">{{ editErrors.max_summary_length }}</div>
            </div>
            <div v-else>{{ agent.max_summary_length }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input type="number" v-model.number="editableAgent.step_delay" />
              <div v-if="editErrors.step_delay" class="field-error">{{ editErrors.step_delay }}</div>
            </div>
            <div v-else>{{ agent.step_delay }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input type="checkbox" v-model="editableAgent.controller_active" />
            </div>
            <div v-else>{{ agent.controller_active }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input type="checkbox" v-model="editableAgent.allow_commands" />
            </div>
            <div v-else>{{ agent.allow_commands }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.prompt" data-test="edit-prompt" />
            </div>
            <div v-else>{{ agent.prompt }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.tasks_input" />
            </div>
            <div v-else>{{ (agent.tasks || []).join(', ') }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.purpose" />
            </div>
            <div v-else>{{ agent.purpose }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.preferred_hardware" />
            </div>
            <div v-else>{{ agent.preferred_hardware }}</div>
          </td>
          <td>
            <div v-if="editingAgent !== name">
              <button @click="startEditing(name, agent)" data-test="edit" aria-label="Agent bearbeiten">Edit</button>
              <button @click="deleteAgent(name)" data-test="delete" aria-label="Agent löschen" :disabled="isSaving">Delete</button>
              <button @click="toggleActive(name)" aria-label="Agent aktiv/inaktiv schalten" :disabled="isSaving">Toggle Active</button>
              <button @click="restartAgent(name)" aria-label="Agent neu starten" :disabled="isSaving">Restart</button>
              <button @click="stopAgent(name)" aria-label="Agent stoppen" :disabled="isSaving">Stop</button>
            </div>
            <div v-else>
              <button @click="saveAgent(name)" :disabled="isSaving || !editValid">Save</button>
              <button @click="cancelEditing" :disabled="isSaving">Cancel</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>

    <div class="pagination" v-if="!isLoading && totalPages > 1">
      <button @click="prevPage" :disabled="page === 1">Zurück</button>
      <span>Seite {{ page }} / {{ totalPages }}</span>
      <button @click="nextPage" :disabled="page === totalPages">Vor</button>
    </div>

    <div class="new-agent">
      <input v-model="newAgent.name" placeholder="Name" data-test="new-name" :aria-invalid="!!newErrors.name" :aria-describedby="newErrors.name ? 'new-name-error' : undefined" />
      <div v-if="newErrors.name" id="new-name-error" class="field-error">{{ newErrors.name }}</div>
      <input v-model="newAgent.model.name" placeholder="model.name" :aria-invalid="!!newErrors['model.name']" :aria-describedby="newErrors['model.name'] ? 'new-model-name-error' : undefined" />
      <div v-if="newErrors['model.name']" id="new-model-name-error" class="field-error">{{ newErrors['model.name'] }}</div>
      <input v-model="newAgent.model.type" placeholder="model.type" />
      <input v-model="newAgent.model.reasoning" placeholder="model.reasoning" />
      <input v-model="newAgent.model_sources" placeholder="model.sources (comma separated)" />
      <select v-model="newAgent.models" multiple data-test="new-models">
        <option v-for="m in modelOptions" :key="m" :value="m">{{ m }}</option>
      </select>
      <div v-if="newErrors.models" class="field-error">{{ newErrors.models }}</div>
      <select v-model="newAgent.template" data-test="new-template">
        <option value=""></option>
        <option v-for="t in templateOptions" :key="t" :value="t">{{ t }}</option>
      </select>
      <div v-if="newErrors.template" class="field-error">{{ newErrors.template }}</div>
      <input type="number" v-model.number="newAgent.max_summary_length" placeholder="max_summary_length" />
      <div v-if="newErrors.max_summary_length" class="field-error">{{ newErrors.max_summary_length }}</div>
      <input type="number" v-model.number="newAgent.step_delay" placeholder="step_delay" />
      <div v-if="newErrors.step_delay" class="field-error">{{ newErrors.step_delay }}</div>
      <label><input type="checkbox" v-model="newAgent.auto_restart" />auto_restart</label>
      <label><input type="checkbox" v-model="newAgent.allow_commands" />allow_commands</label>
      <label><input type="checkbox" v-model="newAgent.controller_active" />controller_active</label>
      <input v-model="newAgent.prompt" placeholder="prompt" data-test="new-prompt" />
      <input v-model="newAgent.tasks_input" placeholder="tasks (comma separated)" />
      <input v-model="newAgent.purpose" placeholder="purpose" />
      <input v-model="newAgent.preferred_hardware" placeholder="preferred_hardware" />
      <button @click="addAgent" data-test="add" :disabled="isSaving || !newValid">Add</button>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed, watch, nextTick } from 'vue';
import { useAppStore } from '../stores/app';
import { get, post } from '../lib/http';

const appStore = useAppStore();

const agents = ref({});
const modelOptions = ref([]);
const templateOptions = ref([]);
const editingAgent = ref(null);
const defaultModel = () => ({ name: '', type: '', reasoning: '', sources: [] });

const isLoading = ref(false);
const isSaving = ref(false);
const loadError = ref('');

// search/sort/filter/pagination state
const searchInput = ref('');
const debouncedSearch = ref('');
let searchTimer = null;
watch(searchInput, (val) => {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { debouncedSearch.value = (val || '').toLowerCase(); page.value = 1; }, 250);
});
const onlyActive = ref(false);
const sortBy = ref('name'); // 'name' | 'template' | 'active'
const sortDir = ref('asc'); // 'asc' | 'desc'
const page = ref(1);
const pageSize = ref(25);

const editErrors = reactive({});
const newErrors = reactive({});
const editFirstInput = ref(null);

const editableAgent = reactive({
  name: '',
  model: defaultModel(),
  models: [],
  template: '',
  max_summary_length: 0,
  step_delay: 0,
  auto_restart: false,
  allow_commands: false,
  controller_active: false,
  prompt: '',
  tasks: [],
  purpose: '',
  preferred_hardware: '',
  model_sources: '',
  tasks_input: ''
});

const newAgent = reactive({
  name: '',
  model: defaultModel(),
  models: [],
  template: '',
  max_summary_length: 0,
  step_delay: 0,
  auto_restart: false,
  allow_commands: false,
  controller_active: false,
  prompt: '',
  tasks: [],
  purpose: '',
  preferred_hardware: '',
  model_sources: '',
  tasks_input: ''
});

const error = ref('');

function validateAgent(agent) {
  const errors = {};
  if (!agent.name || !agent.name.trim()) errors.name = 'Name ist erforderlich.';
  if (!agent.model?.name || !agent.model.name.trim()) errors['model.name'] = 'Model-Name ist erforderlich.';
  if (agent.max_summary_length != null && agent.max_summary_length < 0) errors.max_summary_length = 'Darf nicht negativ sein.';
  if (agent.step_delay != null && agent.step_delay < 0) errors.step_delay = 'Darf nicht negativ sein.';
  // models must be subset of modelOptions
  const setModels = Array.isArray(agent.models) ? agent.models : [];
  const invalidModels = setModels.filter(m => !modelOptions.value.includes(m));
  if (invalidModels.length) errors.models = 'Ungültige Modelle ausgewählt.';
  if (agent.template && !templateOptions.value.includes(agent.template)) errors.template = 'Ungültiges Template.';
  return { valid: Object.keys(errors).length === 0, errors };
}

const editValid = computed(() => {
  if (editingAgent.value == null) return true;
  const payload = { ...editableAgent, name: editableAgent.name };
  const { valid, errors } = validateAgent(payload);
  Object.assign(editErrors, errors);
  for (const k of Object.keys(editErrors)) if (!(k in errors)) delete editErrors[k];
  return valid;
});

const newValid = computed(() => {
  const payload = { ...newAgent, name: newAgent.name };
  const { valid, errors } = validateAgent(payload);
  Object.assign(newErrors, errors);
  for (const k of Object.keys(newErrors)) if (!(k in errors)) delete newErrors[k];
  return valid;
});

const entries = computed(() => Object.entries(agents.value));

const filteredEntries = computed(() => {
  const q = debouncedSearch.value;
  const activeOnly = onlyActive.value;
  return entries.value.filter(([name, a]) => {
    const hay = (name + ' ' + (a.template || '')).toLowerCase();
    if (q && !hay.includes(q)) return false;
    if (activeOnly && !a.controller_active) return false;
    return true;
  });
});

const sortedEntries = computed(() => {
  const arr = [...filteredEntries.value];
  const dir = sortDir.value === 'asc' ? 1 : -1;
  arr.sort((a, b) => {
    let va, vb;
    if (sortBy.value === 'name') { va = a[0]; vb = b[0]; }
    else if (sortBy.value === 'template') { va = a[1]?.template || ''; vb = b[1]?.template || ''; }
    else { va = a[1]?.controller_active ? 1 : 0; vb = b[1]?.controller_active ? 1 : 0; }
    if (va < vb) return -1 * dir;
    if (va > vb) return 1 * dir;
    return 0;
  });
  return arr;
});

const totalPages = computed(() => Math.max(1, Math.ceil(sortedEntries.value.length / pageSize.value)));
const pagedEntries = computed(() => {
  const p = Math.min(page.value, totalPages.value);
  const start = (p - 1) * pageSize.value;
  return sortedEntries.value.slice(start, start + pageSize.value);
});

function setSort(field) {
  if (sortBy.value === field) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc';
  } else {
    sortBy.value = field;
    sortDir.value = 'asc';
  }
}
function sortIcon(field) {
  if (sortBy.value !== field) return '';
  return sortDir.value === 'asc' ? '▲' : '▼';
}
function ariaSort(field) {
  if (sortBy.value !== field) return 'none';
  return sortDir.value === 'asc' ? 'ascending' : 'descending';
}
function nextPage() { if (page.value < totalPages.value) page.value++; }
function prevPage() { if (page.value > 1) page.value--; }

const fetchAgents = async () => {
  isLoading.value = true;
  loadError.value = '';
  try {
    const config = await get('/config');
    const fetchedAgents = config.agents || {};
    agents.value = {};
    for (const [key, val] of Object.entries(fetchedAgents)) {
      agents.value[key] = {
        model: val.model || defaultModel(),
        models: val.models || [],
        template: val.template || '',
        max_summary_length: val.max_summary_length || 0,
        step_delay: val.step_delay || 0,
        auto_restart: val.auto_restart || false,
        allow_commands: val.allow_commands || false,
        controller_active: val.controller_active || false,
        prompt: val.prompt || '',
        tasks: val.tasks || [],
        purpose: val.purpose || '',
        preferred_hardware: val.preferred_hardware || ''
      };
    }
    modelOptions.value = config.models || [];
    templateOptions.value = Object.keys(config.prompt_templates || {});
  } catch (e) {
    console.error('Fehler beim Laden der Agenten-Konfiguration:', e);
    loadError.value = 'Fehler beim Laden der Konfiguration';
  } finally {
    isLoading.value = false;
  }
};

const startEditing = async (name, agent) => {
  editingAgent.value = name;
  editableAgent.name = name;
  editableAgent.model = { ...agent.model };
  editableAgent.model_sources = (agent.model?.sources || []).join(', ');
  editableAgent.models = [...(agent.models || [])];
  editableAgent.template = agent.template || '';
  editableAgent.max_summary_length = agent.max_summary_length || 0;
  editableAgent.step_delay = agent.step_delay || 0;
  editableAgent.auto_restart = !!agent.auto_restart;
  editableAgent.allow_commands = !!agent.allow_commands;
  editableAgent.controller_active = !!agent.controller_active;
  editableAgent.prompt = agent.prompt || '';
  editableAgent.tasks = [...(agent.tasks || [])];
  editableAgent.tasks_input = (agent.tasks || []).join(', ');
  editableAgent.purpose = agent.purpose || '';
  editableAgent.preferred_hardware = agent.preferred_hardware || '';
  await nextTick();
  if (editFirstInput.value) editFirstInput.value.focus();
};

const cancelEditing = () => {
  editingAgent.value = null;
  editableAgent.name = '';
  editableAgent.model = defaultModel();
  editableAgent.model_sources = '';
  editableAgent.models = [];
  editableAgent.template = '';
  editableAgent.max_summary_length = 0;
  editableAgent.step_delay = 0;
  editableAgent.auto_restart = false;
  editableAgent.allow_commands = false;
  editableAgent.controller_active = false;
  editableAgent.prompt = '';
  editableAgent.tasks = [];
  editableAgent.tasks_input = '';
  editableAgent.purpose = '';
  editableAgent.preferred_hardware = '';
};

const persistAgents = async () => {
  isSaving.value = true;
  try {
    await post('/config/agents', { agents: agents.value });
    appStore.pushToast({ type: 'success', message: 'Änderungen gespeichert' });
  } catch (err) {
    console.error('Failed to save agents:', err);
    appStore.pushToast({ type: 'error', message: 'Speichern fehlgeschlagen' });
  } finally {
    isSaving.value = false;
  }
};

const saveAgent = async (name) => {
  const updatedAgent = {
    model: {
      name: editableAgent.model.name,
      type: editableAgent.model.type,
      reasoning: editableAgent.model.reasoning,
      sources: editableAgent.model_sources
        .split(',')
        .map(s => s.trim())
        .filter(Boolean)
    },
    models: [...editableAgent.models],
    template: editableAgent.template,
    max_summary_length: editableAgent.max_summary_length,
    step_delay: editableAgent.step_delay,
    auto_restart: editableAgent.auto_restart,
    allow_commands: editableAgent.allow_commands,
    controller_active: editableAgent.controller_active,
    prompt: editableAgent.prompt,
    tasks: editableAgent.tasks_input
      .split(',')
      .map(t => t.trim())
      .filter(Boolean),
    purpose: editableAgent.purpose,
    preferred_hardware: editableAgent.preferred_hardware
  };
  const nameChanged = editableAgent.name !== name;
  if (nameChanged) {
    delete agents.value[name];
    agents.value[editableAgent.name] = updatedAgent;
  } else {
    agents.value[name] = updatedAgent;
  }
  await persistAgents();
  cancelEditing();
};

const addAgent = async () => {
  if (!newValid.value) return;
  agents.value[newAgent.name] = {
    model: {
      name: newAgent.model.name,
      type: newAgent.model.type,
      reasoning: newAgent.model.reasoning,
      sources: newAgent.model_sources
        .split(',')
        .map(s => s.trim())
        .filter(Boolean)
    },
    models: [...newAgent.models],
    template: newAgent.template,
    max_summary_length: newAgent.max_summary_length,
    step_delay: newAgent.step_delay,
    auto_restart: newAgent.auto_restart,
    allow_commands: newAgent.allow_commands,
    controller_active: newAgent.controller_active,
    prompt: newAgent.prompt,
    tasks: newAgent.tasks_input
      .split(',')
      .map(t => t.trim())
      .filter(Boolean),
    purpose: newAgent.purpose,
    preferred_hardware: newAgent.preferred_hardware
  };
  newAgent.name = '';
  newAgent.model = defaultModel();
  newAgent.model_sources = '';
  newAgent.models = [];
  newAgent.template = '';
  newAgent.max_summary_length = 0;
  newAgent.step_delay = 0;
  newAgent.auto_restart = false;
  newAgent.allow_commands = false;
  newAgent.controller_active = false;
  newAgent.prompt = '';
  newAgent.tasks = [];
  newAgent.tasks_input = '';
  newAgent.purpose = '';
  newAgent.preferred_hardware = '';
  await persistAgents();
};

const deleteAgent = async (name) => {
  delete agents.value[name];
  await persistAgents();
};

const toggleActive = async (name) => {
  try {
    const data = await post(`/agent/${encodeURIComponent(name)}/toggle_active`);
    if (agents.value[name]) {
      agents.value[name].controller_active = !!data.active;
    }
    appStore.pushToast({ type: 'success', message: `${name}: active = ${data.active}` });
  } catch (e) {
    console.error('Toggle active failed:', e);
    appStore.pushToast({ type: 'error', message: `Aktiv-Umschaltung fehlgeschlagen: ${name}` });
  }
};

const restartAgent = async (name) => {
  if (!confirm(`Agent ${name} neu starten?`)) return;
  try {
    await post('/restart', {});
    appStore.pushToast({ type: 'success', message: `Neustart angefordert: ${name}` });
  } catch (e) {
    console.error('Restart failed:', e);
    appStore.pushToast({ type: 'error', message: `Neustart fehlgeschlagen: ${name}` });
  }
};

const stopAgent = async (name) => {
  if (!confirm(`Agent ${name} stoppen?`)) return;
  try {
    await post('/stop', {});
    appStore.pushToast({ type: 'success', message: `Stop angefordert: ${name}` });
  } catch (e) {
    console.error('Stop failed:', e);
    appStore.pushToast({ type: 'error', message: `Stop fehlgeschlagen: ${name}` });
  }
};

onMounted(fetchAgents);
</script>

<style scoped>
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
.controls { display: flex; gap: 1rem; align-items: center; margin-bottom: 0.5rem; }
.banner { display:flex; align-items:center; gap:0.5rem; padding: 8px; margin-bottom: 8px; }
.banner.error { background: #fee2e2; color: #991b1b; }
.loading { margin: 8px 0; }

.table { width: 100%; border-collapse: collapse; }
table, th, td { border: 1px solid #ccc; }
th, td { padding: 8px; text-align: left; }
button { margin-right: 5px; }
.field-error { color: #b91c1c; font-size: 0.85em; }
.pagination { display: flex; align-items: center; gap: 0.5rem; margin: 0.5rem 0; }
</style>

