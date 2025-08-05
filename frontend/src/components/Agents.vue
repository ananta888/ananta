<template>
  <div>
    <h2>Agenten</h2>
    <table>
      <thead>
        <tr>
          <th>Name</th>
          <th>model.name</th>
          <th>model.type</th>
          <th>model.reasoning</th>
          <th>model.sources</th>
          <th>models</th>
          <th>template</th>
          <th>max_summary_length</th>
          <th>step_delay</th>
          <th>auto_restart</th>
          <th>allow_commands</th>
          <th>controller_active</th>
          <th>prompt</th>
          <th>tasks</th>
          <th>purpose</th>
          <th>preferred_hardware</th>
          <th>Aktionen</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(agent, name) in agents" :key="name">
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.name" />
            </div>
            <div v-else>{{ name }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.model.name" />
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
            </div>
            <div v-else>{{ (agent.models || []).join(', ') }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.template" />
            </div>
            <div v-else>{{ agent.template }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input type="number" v-model.number="editableAgent.max_summary_length" />
            </div>
            <div v-else>{{ agent.max_summary_length }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input type="number" v-model.number="editableAgent.step_delay" />
            </div>
            <div v-else>{{ agent.step_delay }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input type="checkbox" v-model="editableAgent.auto_restart" />
            </div>
            <div v-else>{{ agent.auto_restart }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input type="checkbox" v-model="editableAgent.allow_commands" />
            </div>
            <div v-else>{{ agent.allow_commands }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input type="checkbox" v-model="editableAgent.controller_active" />
            </div>
            <div v-else>{{ agent.controller_active }}</div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.prompt" />
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
            <button v-if="editingAgent !== name" @click="startEditing(name, agent)" data-test="edit">Edit</button>
            <button v-if="editingAgent !== name" @click="deleteAgent(name)" data-test="delete">Delete</button>
            <div v-else>
              <button @click="saveAgent(name)">Save</button>
              <button @click="cancelEditing">Cancel</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    <div class="new-agent">
      <input v-model="newAgent.name" placeholder="Name" data-test="new-name" />
      <input v-model="newAgent.model.name" placeholder="model.name" />
      <input v-model="newAgent.model.type" placeholder="model.type" />
      <input v-model="newAgent.model.reasoning" placeholder="model.reasoning" />
      <input v-model="newAgent.model_sources" placeholder="model.sources (comma separated)" />
      <select v-model="newAgent.models" multiple data-test="new-models">
        <option v-for="m in modelOptions" :key="m" :value="m">{{ m }}</option>
      </select>
      <input v-model="newAgent.template" placeholder="template" />
      <input type="number" v-model.number="newAgent.max_summary_length" placeholder="max_summary_length" />
      <input type="number" v-model.number="newAgent.step_delay" placeholder="step_delay" />
      <label><input type="checkbox" v-model="newAgent.auto_restart" />auto_restart</label>
      <label><input type="checkbox" v-model="newAgent.allow_commands" />allow_commands</label>
      <label><input type="checkbox" v-model="newAgent.controller_active" />controller_active</label>
      <input v-model="newAgent.prompt" placeholder="prompt" />
      <input v-model="newAgent.tasks_input" placeholder="tasks (comma separated)" />
      <input v-model="newAgent.purpose" placeholder="purpose" />
      <input v-model="newAgent.preferred_hardware" placeholder="preferred_hardware" />
      <button @click="addAgent" data-test="add">Add</button>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue';

const agents = ref({});
const modelOptions = ref([]);
const editingAgent = ref(null);
const defaultModel = () => ({ name: '', type: '', reasoning: '', sources: [] });

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

const fetchAgents = async () => {
  try {
    const response = await fetch('/config');
    const config = await response.json();
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
  } catch (error) {
    console.error('Fehler beim Laden der Agenten-Konfiguration:', error);
  }
};

const startEditing = (name, agent) => {
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
  try {
    await fetch('/config/agents', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agents: agents.value })
    });
  } catch (err) {
    console.error('Failed to save agents:', err);
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
  if (editableAgent.name !== name) {
    delete agents.value[name];
    agents.value[editableAgent.name] = updatedAgent;
  } else {
    agents.value[name] = updatedAgent;
  }
  await persistAgents();
  cancelEditing();
};

const addAgent = async () => {
  if (!newAgent.name) return;
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

onMounted(fetchAgents);
</script>

<style scoped>
table {
  width: 100%;
  border-collapse: collapse;
}
table, th, td {
  border: 1px solid #ccc;
}
th, td {
  padding: 8px;
  text-align: left;
}
button {
  margin-right: 5px;
}
</style>

