<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'

const logs = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const action = ref('')
const decision = ref('')
const err = ref('')

async function load() {
  const p = new URLSearchParams({ page: String(page.value), page_size: '50' })
  if (action.value) p.set('action', action.value)
  if (decision.value) p.set('decision', decision.value)
  try {
    const res = await api<any>(`/api/v1/admin/audit-logs?${p}`)
    logs.value = res.logs
    total.value = res.total
  } catch (e: any) {
    err.value = e.message
  }
}

function clsOf(l: any) {
  return l.decision === 'deny' ? 'danger' : 'ok'
}

onMounted(load)
</script>

<template>
  <div class="layout">
    <div class="card">
      <div class="row">
        <b>审计日志</b>
        <span class="muted">共 {{ total }} 条</span>
        <select v-model="action" style="width: 150px" @change="page = 1; load()">
          <option value="">全部操作</option>
          <option value="login">login</option>
          <option value="search">search</option>
          <option value="view">view</option>
          <option value="download">download</option>
          <option value="upload">upload</option>
          <option value="permission_change">permission_change</option>
          <option value="scan_enhance">scan_enhance</option>
          <option value="reindex">reindex</option>
        </select>
        <select v-model="decision" style="width: 120px" @change="page = 1; load()">
          <option value="">全部判定</option>
          <option value="allow">allow</option>
          <option value="deny">deny</option>
        </select>
        <button class="btn" @click="load">刷新</button>
      </div>
    </div>
    <p v-if="err" class="card err">{{ err }}</p>
    <div class="card">
      <table>
        <thead>
          <tr>
            <th>时间</th><th>操作者</th><th>操作</th><th>对象</th>
            <th>密级</th><th>判定</th><th>HTTP</th><th>耗时</th><th>request_id</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="l in logs" :key="l.id">
            <td class="muted">{{ l.ts.slice(5, 19) }}</td>
            <td>{{ l.actor || '-' }}</td>
            <td>{{ l.action }}</td>
            <td>{{ l.resource_type }}{{ l.resource_id ? `#${l.resource_id}` : '' }}</td>
            <td>{{ l.classification ?? '-' }}</td>
            <td><span class="badge" :class="clsOf(l)">{{ l.decision }}</span></td>
            <td>{{ l.status_code }}</td>
            <td class="muted">{{ l.latency_ms }}ms</td>
            <td class="muted mono" :title="l.request_id">{{ l.request_id.slice(0, 8) }}</td>
          </tr>
          <tr v-if="!logs.length">
            <td colspan="9" class="muted" style="text-align: center">暂无日志</td>
          </tr>
        </tbody>
      </table>
      <div class="row" style="justify-content: flex-end; margin-top: 10px">
        <button class="btn" :disabled="page <= 1" @click="page--; load()">上一页</button>
        <span class="muted">第 {{ page }} 页</span>
        <button class="btn" :disabled="page * 50 >= total" @click="page++; load()">下一页</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mono { font-family: monospace; }
.err { color: var(--danger); }
</style>
