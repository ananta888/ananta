<template>
  <div>
    <h2>Agenten</h2>
    <table>
      <thead>
        <tr>
          <th>Name</th>
          <th>Modell</th>
          <th>Zweck</th>
          <th>Hardware</th>
          <th>Modell-Typ</th>
          <th>Begründung</th>
          <th>Quellen</th>
          <th>Aktionen</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(agent, name) in agents" :key="name">
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.name" />
            </div>
            <div v-else>
              {{ name }}
            </div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <select v-model="editableAgent.model">
                <option v-for="m in modelOptions" :key="m" :value="m">{{ m }}</option>
              </select>
            </div>
            <div v-else>
              {{ agent.model }}
            </div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.purpose" />
            </div>
            <div v-else>
              {{ agent.purpose }}
            </div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.preferred_hardware" />
            </div>
            <div v-else>
              {{ agent.preferred_hardware }}
            </div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.model_type" />
            </div>
            <div v-else>
              {{ agent.model_info?.type }}
            </div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.model_reasoning" />
            </div>
            <div v-else>
              {{ agent.model_info?.reasoning }}
            </div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.model_sources" />
            </div>
            <div v-else>
              {{ (agent.model_info?.sources || []).join(', ') }}
            </div>
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
      <select v-model="newAgent.model" data-test="new-model">
        <option v-for="m in modelOptions" :key="m" :value="m">{{ m }}</option>
      </select>
      <input v-model="newAgent.purpose" placeholder="Zweck" />
      <input v-model="newAgent.preferred_hardware" placeholder="Hardware" />
      <input v-model="newAgent.model_type" placeholder="Modell-Typ" />
      <input v-model="newAgent.model_reasoning" placeholder="Begründung" />
      <input v-model="newAgent.model_sources" placeholder="Quellen (kommagetrennt)" />
      <button @click="addAgent" data-test="add">Add</button>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue';

const agents = ref({});
const modelOptions = ref([]);
const editingAgent = ref(null);
const editableAgent = reactive({
  name: '',
  model: '',
  purpose: '',
  preferred_hardware: '',
  model_type: '',
  model_reasoning: '',
  model_sources: ''
});
const newAgent = reactive({
  name: '',
  model: '',
  purpose: '',
  preferred_hardware: '',
  model_type: '',
  model_reasoning: '',
  model_sources: ''
});

const fetchAgents = async () => {
  try {
    const response = await fetch('/config');
    const config = await response.json();
    const fetchedAgents = config.agents || {};
    agents.value = {};
    for (const [key, val] of Object.entries(fetchedAgents)) {
      const { provider, ...rest } = val;
      agents.value[key] = rest;
    }
    modelOptions.value = config.models || [];
  } catch (error) {
    console.error('Fehler beim Laden der Agenten-Konfiguration:', error);
  }
};

const startEditing = (name, agent) => {
  editingAgent.value = name;
  editableAgent.name = name;
  editableAgent.model = agent.model;
  editableAgent.purpose = agent.purpose || '';
  editableAgent.preferred_hardware = agent.preferred_hardware || '';
  editableAgent.model_type = agent.model_info?.type || '';
  editableAgent.model_reasoning = agent.model_info?.reasoning || '';
  editableAgent.model_sources = (agent.model_info?.sources || []).join(', ');
};

const cancelEditing = () => {
  editingAgent.value = null;
  editableAgent.name = '';
  editableAgent.model = '';
  editableAgent.purpose = '';
  editableAgent.preferred_hardware = '';
  editableAgent.model_type = '';
  editableAgent.model_reasoning = '';
  editableAgent.model_sources = '';
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
    ...agents.value[name],
    model: editableAgent.model,
    purpose: editableAgent.purpose,
    preferred_hardware: editableAgent.preferred_hardware,
    model_info: {
      ...(agents.value[name].model_info || {}),
      type: editableAgent.model_type,
      reasoning: editableAgent.model_reasoning,
      sources: editableAgent.model_sources
        .split(',')
        .map(s => s.trim())
        .filter(Boolean)
    }
  };
  delete updatedAgent.provider;
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
    model: newAgent.model,
    purpose: newAgent.purpose,
    preferred_hardware: newAgent.preferred_hardware,
    model_info: {
      type: newAgent.model_type,
      reasoning: newAgent.model_reasoning,
      sources: newAgent.model_sources
        .split(',')
        .map(s => s.trim())
        .filter(Boolean)
    }
  };
  newAgent.name = '';
  newAgent.model = '';
  newAgent.purpose = '';
  newAgent.preferred_hardware = '';
  newAgent.model_type = '';
  newAgent.model_reasoning = '';
  newAgent.model_sources = '';
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
