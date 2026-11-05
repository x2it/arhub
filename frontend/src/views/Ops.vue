<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, authFetch, readError } from '../api'

const ready = ref<any>(null)
const stats = ref<any>(null)
const backupMsg = ref('')
const busy = ref(false)
const err = ref('')

async function refresh() {
  try {
    ready.value = await api('/ready')
    stats.value = await api('/api/v1/stats')
  } catch (e: any) {
    err.value = e.message
  }
}

async function runBackup() {
  busy.value = true
  backupMsg.value = ''
  try {
    const res = await authFetch('/api/v1/admin/backup', { method: 'POST' })
    if (!res.ok) {
      backupMsg.value = `❌ ${await readError(res)}`
      return
    }
    const data = await res.json().catch(() => ({}))
    backupMsg.value = `✅ ${data.message || '备份完成'}`
  } catch (e: any) {
    backupMsg.value = `❌ ${e.message}`
  } finally {
    busy.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <div class="layout">
    <div class="card">
      <div class="row" style="justify-content: space-between">
        <h3 style="margin: 0">🛠 运维台</h3>
        <button class="btn" @click="refresh">刷新状态</button>
      </div>
    </div>
    <p v-if="err" class="card err">{{ err }}</p>
    <div v-if="ready" class="card">
      <h3 style="margin-top: 0">依赖健康（readiness）</h3>
      <table>
        <tr v-for="(v, k) in ready.checks" :key="k">
          <td style="width: 160px">{{ k }}</td>
          <td><span class="badge" :class="v === 'ok' ? 'ok' : 'danger'">{{ v }}</span></td>
        </tr>
      </table>
    </div>
    <div v-if="stats" class="card">
      <h3 style="margin-top: 0">索引队列</h3>
      <div class="row">
        <span>待处理 <b>{{ stats.index_queue?.pending }}</b></span>
        <span>死信 <b :class="stats.index_queue?.dead ? 'err' : ''">{{ stats.index_queue?.dead }}</b></span>
        <span>审计落库失败 <b :class="stats.audit_dropped ? 'warn' : ''">{{ stats.audit_dropped }}</b></span>
      </div>
      <p class="muted" style="font-size: 13px; margin-bottom: 4px">
        死信任务可由管理员触发对账重建（POST /api/v1/index）自动重试恢复。
      </p>
      <div class="row">
        <button
          class="btn"
          @click="api('/api/v1/index', { method: 'POST' }).then(() => (backupMsg = '已触发全量对账'))"
        >
          触发全量对账
        </button>
      </div>
    </div>
    <div class="card">
      <h3 style="margin-top: 0">备份与恢复</h3>
      <p class="muted" style="font-size: 13px">
        线上操作通过 SSH 执行 <code>scripts/backup.sh</code> / <code>scripts/restore.sh</code>；
        此处提供网关触发的便捷备份入口。
      </p>
      <div class="row">
        <button class="btn primary" :disabled="busy" @click="runBackup">
          <span v-if="busy" class="spinner"></span> 立即备份
        </button>
        <span v-if="backupMsg">{{ backupMsg }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.err { color: var(--danger); }
.warn { color: var(--warn); }
</style>
