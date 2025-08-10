import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import { useAppStore } from './stores/app';

const app = createApp(App);
const pinia = createPinia();
app.use(pinia);

// Initialize theme on load
const store = useAppStore();
store.initTheme();

app.mount('#app');
