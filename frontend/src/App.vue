<template>
  <div class="container" role="main">
    <header class="header">
      <h1>Agent Controller Dashboard</h1>
      <ThemeSwitcher />
    </header>
    <nav class="tabs" role="tablist" aria-label="Ansichten">
      <button
        v-for="tab in tabs"
        :key="tab"
        @click="currentTab = tab"
        :class="{ active: currentTab === tab }"
        role="tab"
        :aria-selected="currentTab === tab"
        :tabindex="currentTab === tab ? 0 : -1"
        :data-testid="'tab-' + tab.toLowerCase().replace(/\s+/g, '-')"
      >
        {{ tab }}
      </button>
    </nav>
    <div class="card">
      <component :is="tabComponents[currentTab]" />
    </div>
    <Toasts />
  </div>
</template>

<script setup>
import { ref } from 'vue';
import ThemeSwitcher from './components/ThemeSwitcher.vue';
import Toasts from './components/Toasts.vue';
import Pipeline from './components/Pipeline.vue';
import Agents from './components/Agents.vue';
import Tasks from './components/Tasks.vue';
import AgentTaskOverview from './components/AgentTaskOverview.vue';
import Templates from './components/Templates.vue';
import Endpoints from './components/Endpoints.vue';
import Models from './components/Models.vue';
import Settings from './components/Settings.vue';
import AgentLogViewer from './components/AgentLogViewer.vue';
import DbContents from './components/DbContents.vue';

const tabs = ['Pipeline', 'Agents', 'Tasks', 'Agent Tasks', 'Templates', 'Endpoints', 'Models', 'Einstellungen', 'Logs', 'DB'];
const tabComponents = { Pipeline, Agents, Tasks, 'Agent Tasks': AgentTaskOverview, Templates, Endpoints, Models, Einstellungen: Settings, Logs: AgentLogViewer, DB: DbContents };
const currentTab = ref('Pipeline');
</script>
