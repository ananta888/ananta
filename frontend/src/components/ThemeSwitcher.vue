<template>
  <div class="theme-switcher" role="group" aria-label="Theme umschalten">
    <label :for="idLight" class="sr-only">Helles Theme</label>
    <input :id="idLight" type="radio" name="theme" value="light" :checked="theme === 'light'" @change="onChange('light')" />
    <label :for="idLight">Light</label>

    <label :for="idDark" class="sr-only">Dunkles Theme</label>
    <input :id="idDark" type="radio" name="theme" value="dark" :checked="theme === 'dark'" @change="onChange('dark')" />
    <label :for="idDark">Dark</label>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useAppStore } from '../stores/app';

const store = useAppStore();
const theme = computed(() => store.theme);
const idLight = 'theme-light';
const idDark = 'theme-dark';

function onChange(val) {
  if (val !== store.theme) {
    store.setTheme(val);
  }
}
</script>

<style scoped>
.theme-switcher {
  display: inline-flex;
  gap: 0.5rem;
  align-items: center;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0,0,0,0);
  white-space: nowrap;
  border: 0;
}
</style>
