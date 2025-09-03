<template>
  <section>
    <h2>DB Contents</h2>
    <div class="controls">
      <label>
        Table:
        <input v-model="table" placeholder="optional table name" />
      </label>
      <label>
        Limit:
        <input type="number" v-model.number="limit" min="1" max="1000" />
      </label>
      <label>
        Offset:
        <input type="number" v-model.number="offset" min="0" />
      </label>
      <label>
        <input type="checkbox" v-model="includeEmpty" /> Include empty tables
      </label>
      <button @click="reload" :disabled="loading">Reload</button>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="loading">Loading...</p>

    <div v-if="data && data.tables && data.tables.length">
      <div v-for="(t, idx) in data.tables" :key="idx" class="table-block">
        <h3>{{ t.name }}</h3>
        <div class="table-wrapper">
          <table>
            <thead>
              <tr>
                <th v-for="(c, i) in t.columns" :key="i">{{ c }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, rIndex) in t.rows" :key="rIndex">
                <td v-for="(c, i) in t.columns" :key="i">{{ formatCell(row[c]) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div class="pager">
        <button @click="prevPage" :disabled="offset <= 0 || loading">Prev</button>
        <span>offset={{ offset }}, limit={{ limit }}</span>
        <button @click="nextPage" :disabled="loading">Next</button>
      </div>
    </div>

    <div v-else-if="!loading && !error" class="empty">No tables to display.</div>
  </section>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const loading = ref(false)
const error = ref('')
const data = ref(null)

const limit = ref(100)
const offset = ref(0)
const table = ref('')
const includeEmpty = ref(false)

function buildUrl() {
  const params = new URLSearchParams()
  if (table.value && table.value.trim()) params.set('table', table.value.trim())
  if (limit.value) params.set('limit', String(Math.max(1, Math.min(1000, limit.value))))
  if (offset.value) params.set('offset', String(Math.max(0, offset.value)))
  if (includeEmpty.value) params.set('include_empty', '1')
  return `/db/contents?${params.toString()}`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(buildUrl())
    if (!res.ok) {
      const text = (typeof res.text === 'function') ? await res.text() : ''
      throw new Error(text || `HTTP ${res.status}`)
    }
    const json = await res.json()
    data.value = json
  } catch (e) {
    error.value = 'Fehler beim Laden der DB-Inhalte'
  } finally {
    loading.value = false
  }
}

function reload() {
  offset.value = Math.max(0, Number(offset.value) || 0)
  limit.value = Math.max(1, Math.min(1000, Number(limit.value) || 100))
  load()
}

function prevPage() {
  offset.value = Math.max(0, (Number(offset.value) || 0) - (Number(limit.value) || 100))
  load()
}

function nextPage() {
  offset.value = (Number(offset.value) || 0) + (Number(limit.value) || 100)
  load()
}

function formatCell(v) {
  if (v === null || v === undefined) return ''
  if (typeof v === 'object') {
    try { return JSON.stringify(v) } catch (_) { return String(v) }
  }
  return String(v)
}

onMounted(load)
</script>

<style scoped>
.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1rem;
  align-items: center;
  margin-bottom: 1rem;
}
.controls input[type="number"],
.controls input[type="text"],
.controls input[type="search"] {
  max-width: 10rem;
}
.error { color: red; }
.empty { color: #666; }
.table-block { margin-bottom: 1.5rem; }
.table-wrapper { overflow: auto; max-width: 100%; border: 1px solid #ddd; }
.table-wrapper table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
.table-wrapper th, .table-wrapper td { border: 1px solid #eee; padding: 4px 6px; text-align: left; }
.pager { display: flex; gap: 1rem; align-items: center; }
</style>
