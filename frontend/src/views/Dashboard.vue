<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'

const stats = ref<any>(null)
const ready = ref<any>(null)
const err = ref('')

onMounted(async () => {
  try {
    stats.value = await api('/api/v1/stats')
    ready.value = await api('/ready')
  } catch (e: any) {
    err.value = e.message
  }
})
</script>

<template>
  <div class="layout">
    <p v-if="err" class="card err">{{ err }}</p>
    <template v-if="stats">
      <div class="tiles">
        <div class="card tile">
          <div class="num">{{ stats.total_documents ?? '-' }}</div>
          <div class="muted">归档总数</div>
        </div>
        <div class="card tile">
          <div class="num">{{ stats.semantic_index?.documents ?? 0 }}</div>
          <div class="muted">语义索引文档</div>
        </div>
        <div class="card tile">
          <div class="num">{{ stats.semantic_index?.chunks ?? 0 }}</div>
          <div class="muted">语义向量片段</div>
        </div>
        <div class="card tile">
          <div class="num" :class="{ warn: stats.index_queue?.pending > 0 }">
            {{ stats.index_queue?.pending ?? 0 }}
          </div>
          <div class="muted">索引队列待处理</div>
        </div>
        <div class="card tile">
          <div class="num" :class="{ err: stats.index_queue?.dead > 0 }">
            {{ stats.index_queue?.dead ?? 0 }}
          </div>
          <div class="muted">死信任务</div>
        </div>
        <div class="card tile">
          <div class="num" :class="{ warn: stats.audit_dropped > 0 }">
            {{ stats.audit_dropped }}
          </div>
          <div class="muted">审计落库失败</div>
        </div>
      </div>
      <div class="card" v-if="ready">
        <h3 style="margin-top: 0">服务健康</h3>
        <table>
          <tr v-for="(v, k) in ready.checks" :key="k">
            <td>{{ k }}</td>
            <td><span class="badge" :class="v === 'ok' ? 'ok' : 'danger'">{{ v }}</span></td>
          </tr>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}
.tile { text-align: center; }
.num { font-size: 30px; font-weight: 700; }
.num.warn { color: var(--warn); }
.num.err { color: var(--danger); }
.err { color: var(--danger); }
</style>
