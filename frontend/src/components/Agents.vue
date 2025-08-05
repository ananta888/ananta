<template>
  <div>
    <h2>Agenten</h2>
    <table>
      <thead>
        <tr>
          <th>Name</th>
          <th>Modell</th>
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
      <button @click="addAgent" data-test="add">Add</button>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue';

const agents = ref({});
const modelOptions = ref([]);
const editingAgent = ref(null);
const editableAgent = reactive({ name: '', model: '' });
const newAgent = reactive({ name: '', model: '' });

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
};

const cancelEditing = () => {
  editingAgent.value = null;
  editableAgent.name = '';
  editableAgent.model = '';
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
    model: editableAgent.model
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
  agents.value[newAgent.name] = { model: newAgent.model };
  newAgent.name = '';
  newAgent.model = '';
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
