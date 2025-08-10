<template>
  <div class="toasts" aria-live="polite" aria-atomic="true">
    <div v-for="t in toasts" :key="t.id" class="toast" :class="t.type" role="status">
      <span class="msg">{{ t.message }}</span>
      <button class="close" @click="remove(t.id)" aria-label="Schließen">×</button>
    </div>
  </div>
</template>

<script setup>
import { storeToRefs } from 'pinia';
import { useAppStore } from '../stores/app';

const store = useAppStore();
const { toasts } = storeToRefs(store);
function remove(id) { store.removeToast(id); }
</script>

<style scoped>
.toasts {
  position: fixed;
  right: 1rem;
  bottom: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  z-index: 1000;
}
.toast {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  background: #111827;
  color: #fff;
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.2);
  min-width: 220px;
}
.toast.info { background: #2563eb; }
.toast.success { background: #16a34a; }
.toast.error { background: #dc2626; }
.toast .close {
  margin-left: auto;
  background: transparent;
  border: none;
  color: inherit;
  font-size: 1.2rem;
  cursor: pointer;
}
</style>
