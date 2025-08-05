<template>
  <section>
    <h2>Templates</h2>
    <div v-for="(text, name) in templates" :key="name" class="template">
      <label>{{ name }}</label>
      <textarea v-model="templates[name]"></textarea>
      <button @click="deleteTemplate(name)">Delete</button>
    </div>
    <div class="template-form">
      <input v-model="newTemplate.name" placeholder="name" />
      <textarea v-model="newTemplate.text" placeholder="template"></textarea>
      <button @click="addTemplate">Add</button>
    </div>
    <button @click="saveTemplates">Save Templates</button>
  </section>
</template>

<script setup>
import { ref, onMounted } from 'vue';

const base = '';
const templates = ref({});
const newTemplate = ref({ name: '', text: '' });

async function loadTemplates() {
  const res = await fetch(base + '/config');
  const data = await res.json();
  templates.value = JSON.parse(JSON.stringify(data.prompt_templates || {}));
}

function addTemplate() {
  if (newTemplate.value.name) {
    templates.value[newTemplate.value.name] = newTemplate.value.text;
    newTemplate.value = { name: '', text: '' };
  }
}

function deleteTemplate(name) {
  delete templates.value[name];
}

async function saveTemplates() {
  const form = new FormData();
  form.append('prompt_templates', JSON.stringify(templates.value));
  await fetch(base + '/', { method: 'POST', body: form });
  await loadTemplates();
}

onMounted(loadTemplates);
</script>

<style scoped>
.template {
  border: 1px solid #ddd;
  padding: 10px;
  margin-bottom: 10px;
}
textarea {
  width: 100%;
  min-height: 60px;
}
</style>
