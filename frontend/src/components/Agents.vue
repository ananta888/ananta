<template>
  <div>
    <h2>Agenten</h2>
    <table>
      <thead>
        <tr>
          <th>Name</th>
          <th>Modell</th>
          <th>Provider</th>
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
              <input v-model="editableAgent.model" />
            </div>
            <div v-else>
              {{ agent.model }}
            </div>
          </td>
          <td>
            <div v-if="editingAgent === name">
              <input v-model="editableAgent.provider" />
            </div>
            <div v-else>
              {{ agent.provider }}
            </div>
          </td>
          <td>
            <button v-if="editingAgent !== name" @click="startEditing(name, agent)">Bearbeiten</button>
            <div v-else>
              <button @click="saveAgent(name)">Speichern</button>
              <button @click="cancelEditing">Abbrechen</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue';

// Reaktive Datenstruktur, um die Agenten zu speichern
const agents = ref({});

// Variable, welche angibt, welcher Agent gerade editiert wird (basierend auf dem Namen)
const editingAgent = ref(null);

// Temporärer Speicher für die editierbaren Felder
const editableAgent = reactive({
  name: '',
  model: '',
  provider: ''
});

// Funktion zum Laden der Agenten-Konfiguration aus dem Backend
const fetchAgents = async () => {
  try {
    const response = await fetch('/config');
    const config = await response.json();
    agents.value = config.agents || {};
  } catch (error) {
    console.error('Fehler beim Laden der Agenten-Konfiguration:', error);
  }
};

// Funktion zum Starten des Editiermodus eines Agenten
const startEditing = (name, agent) => {
  editingAgent.value = name;
  editableAgent.name = name;
  editableAgent.model = agent.model;
  editableAgent.provider = agent.provider;
};

// Funktion zum Abbrechen des Editiermodus
const cancelEditing = () => {
  editingAgent.value = null;
  editableAgent.name = '';
  editableAgent.model = '';
  editableAgent.provider = '';
};

// Funktion zum Speichern der Änderungen des Agenten
const saveAgent = async (name) => {
  // Aktualisiere die Daten lokal
  const updatedAgent = {
    ...agents.value[name],
    model: editableAgent.model,
    provider: editableAgent.provider
  };
  // Wenn der Name verändert wurde, muss der Schlüssel im Objekt aktualisiert werden
  if (editableAgent.name !== name) {
    delete agents.value[name];
    agents.value[editableAgent.name] = updatedAgent;
  } else {
    agents.value[name] = updatedAgent;
  }
  // Hier könnte ein Request an das Backend erfolgen, um die Änderungen zu speichern.
  // Beispiel:
  // await fetch('/update-agent-config', {
  //   method: 'POST',
  //   headers: { 'Content-Type': 'application/json' },
  //   body: JSON.stringify({ agents: agents.value })
  // });
  console.log('Gespeicherter Agent:', editingAgent.value, updatedAgent);
  cancelEditing();
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