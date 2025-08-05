<template>
  <div>
    <h2>API Endpoints</h2>
    <table>
      <thead>
        <tr>
          <th>Type</th>
          <th>URL</th>
          <th>Models</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(endpoint, index) in endpoints" :key="index">
          <td>
            <div v-if="editingIndex === index">
              <input v-model="editableEndpoint.type" />
            </div>
            <div v-else>
              {{ endpoint.type }}
            </div>
          </td>
          <td>
            <div v-if="editingIndex === index">
              <input v-model="editableEndpoint.url" />
            </div>
            <div v-else>
              {{ endpoint.url }}
            </div>
          </td>
          <td>
            <div v-if="editingIndex === index">
              <select v-model="editableEndpoint.models" multiple data-test="edit-models">
                <option v-for="m in modelOptions" :key="m" :value="m">{{ m }}</option>
              </select>
            </div>
            <div v-else>
              {{ endpoint.models.join(', ') }}
            </div>
          </td>
          <td>
            <button v-if="editingIndex !== index" @click="startEditing(index, endpoint)" data-test="edit">Edit</button>
            <button v-if="editingIndex !== index" @click="deleteEndpoint(index)" data-test="delete">Delete</button>
            <div v-else>
              <button @click="saveEndpoint(index)">Save</button>
              <button @click="cancelEditing">Cancel</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    <div class="new-endpoint">
      <input v-model="newEndpoint.type" placeholder="Type" data-test="new-type" />
      <input v-model="newEndpoint.url" placeholder="URL" data-test="new-url" />
      <select v-model="newEndpoint.models" multiple data-test="new-models">
        <option v-for="m in modelOptions" :key="m" :value="m">{{ m }}</option>
      </select>
      <button @click="addEndpoint" data-test="add">Add</button>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue';

const endpoints = ref([]);
const editingIndex = ref(null);
const modelOptions = ref([]);
const editableEndpoint = reactive({ type: '', url: '', models: [] });
const newEndpoint = reactive({ type: '', url: '', models: [] });

const fetchEndpoints = async () => {
  try {
    const response = await fetch('/config');
    const config = await response.json();
    endpoints.value = config.api_endpoints || [];
    modelOptions.value = config.models || [];
  } catch (error) {
    console.error('Failed to load endpoints:', error);
  }
};

const startEditing = (index, endpoint) => {
  editingIndex.value = index;
  editableEndpoint.type = endpoint.type;
  editableEndpoint.url = endpoint.url;
  editableEndpoint.models = [...(endpoint.models || [])];
};

const cancelEditing = () => {
  editingIndex.value = null;
  editableEndpoint.type = '';
  editableEndpoint.url = '';
  editableEndpoint.models = [];
};

const saveEndpoint = async (index) => {
  endpoints.value[index] = {
    type: editableEndpoint.type,
    url: editableEndpoint.url,
    models: editableEndpoint.models.slice(),
  };
  await persistEndpoints();
  cancelEditing();
};

const addEndpoint = async () => {
  if (!newEndpoint.url) return;
  endpoints.value.push({
    type: newEndpoint.type,
    url: newEndpoint.url,
    models: newEndpoint.models.slice(),
  });
  newEndpoint.type = '';
  newEndpoint.url = '';
  newEndpoint.models = [];
  await persistEndpoints();
};

const deleteEndpoint = async (index) => {
  endpoints.value.splice(index, 1);
  await persistEndpoints();
};

const persistEndpoints = async () => {
  try {
    await fetch('/config/api_endpoints', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_endpoints: endpoints.value })
    });
  } catch (error) {
    console.error('Failed to save endpoints:', error);
  }
};

onMounted(fetchEndpoints);
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
