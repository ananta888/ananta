<template>
  <div>
    <h2>Models</h2>
    <ul>
      <li v-for="(model, index) in models" :key="index">
        {{ model }}
        <button @click="removeModel(index)" data-test="delete">Delete</button>
      </li>
    </ul>
    <input v-model="newModel" placeholder="New model" data-test="new-name" />
    <button @click="addModel" data-test="add">Add</button>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';

const models = ref([]);
const newModel = ref('');

const fetchModels = async () => {
  try {
    const response = await fetch('/config');
    const config = await response.json();
    models.value = config.models || [];
  } catch (err) {
    console.error('Failed to load models:', err);
  }
};

const persistModels = async () => {
  try {
    await fetch('/config/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ models: models.value })
    });
  } catch (err) {
    console.error('Failed to save models:', err);
  }
};

const addModel = async () => {
  if (!newModel.value) return;
  models.value.push(newModel.value);
  newModel.value = '';
  await persistModels();
};

const removeModel = async (index) => {
  models.value.splice(index, 1);
  await persistModels();
};

onMounted(fetchModels);
</script>

<style scoped>
ul {
  padding: 0;
}
li {
  list-style: none;
  margin-bottom: 4px;
}
button {
  margin-left: 8px;
}
</style>
