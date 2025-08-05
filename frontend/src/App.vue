<template>
  <div class="container">
    <h1>Agent Controller Dashboard</h1>
    <nav class="tabs">
      <button v-for="tab in tabs" :key="tab" @click="currentTab = tab" :class="{ active: currentTab === tab }">
        {{ tab }}
      </button>
    </nav>
    <component :is="tabComponents[currentTab]" />
  </div>
</template>

<script setup>
import { ref } from 'vue';
import Pipeline from './components/Pipeline.vue';
import Agents from './components/Agents.vue';
import Tasks from './components/Tasks.vue';
import Templates from './components/Templates.vue';
import Endpoints from './components/Endpoints.vue';

const tabs = ['Pipeline', 'Agents', 'Tasks', 'Templates', 'Endpoints'];
const tabComponents = { Pipeline, Agents, Tasks, Templates, Endpoints };
const currentTab = ref('Pipeline');
</script>

<style>
.container {
  font-family: Arial, sans-serif;
  margin: 20px;
}
.tabs {
  margin-bottom: 20px;
}
.tabs button {
  margin-right: 10px;
  padding: 5px 10px;
}
.tabs button.active {
  font-weight: bold;
}
</style>
