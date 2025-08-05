<template>
  <div>
    <h2>API Endpoints</h2>
    <table>
      <thead>
        <tr>
          <th>Type</th>
          <th>URL</th>
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
            <button v-if="editingIndex !== index" @click="startEditing(index, endpoint)">Edit</button>
            <div v-else>
              <button @click="saveEndpoint(index)">Save</button>
              <button @click="cancelEditing">Cancel</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue';

const endpoints = ref([]);
const editingIndex = ref(null);
const editableEndpoint = reactive({ type: '', url: '' });

const fetchEndpoints = async () => {
  try {
    const response = await fetch('/config');
    const config = await response.json();
    endpoints.value = config.api_endpoints || [];
  } catch (error) {
    console.error('Failed to load endpoints:', error);
  }
};

const startEditing = (index, endpoint) => {
  editingIndex.value = index;
  editableEndpoint.type = endpoint.type;
  editableEndpoint.url = endpoint.url;
};

const cancelEditing = () => {
  editingIndex.value = null;
  editableEndpoint.type = '';
  editableEndpoint.url = '';
};

const saveEndpoint = async (index) => {
  endpoints.value[index] = { ...editableEndpoint };
  // Placeholder for future backend update request
  cancelEditing();
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
