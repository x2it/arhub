<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api, CLASS_NAMES } from '../api'

const users = ref<any[]>([])
const err = ref('')
const toast = ref('')
const form = reactive({
  username: '',
  password: '',
  display_name: '',
  dept: '',
  clearance: 1,
  roles: 'viewer',
})
const secForm = reactive({ docId: '', classification: 1 })

async function load() {
  try {
    users.value = (await api('/api/v1/admin/users')).users
  } catch (e: any) {
    err.value = e.message
  }
}

async function createUser() {
  const body = new URLSearchParams({ ...form, roles: form.roles } as any)
  try {
    await api('/api/v1/admin/users', { method: 'POST', body })
    toast.value = '用户已创建'
    Object.assign(form, { username: '', password: '', display_name: '', dept: '' })
    await load()
  } catch (e: any) {
    toast.value = e.message
  }
  setTimeout(() => (toast.value = ''), 2500)
}

async function toggleStatus(u: any) {
  const body = new URLSearchParams({ status: u.status === 'active' ? 'disabled' : 'active' })
  try {
    await api(`/api/v1/admin/users/${u.id}`, { method: 'PATCH', body })
    await load()
  } catch (e: any) {
    alert(e.message)
  }
}

async function setClearance(u: any, level: number) {
  const body = new URLSearchParams({ clearance: String(level) })
  try {
    await api(`/api/v1/admin/users/${u.id}`, { method: 'PATCH', body })
    await load()
  } catch (e: any) {
    alert(e.message)
  }
}

async function setDocSecurity() {
  if (!secForm.docId) return
  const body = new URLSearchParams({ classification: String(secForm.classification) })
  try {
    await api(`/api/v1/admin/docs/${secForm.docId}/security`, { method: 'PUT', body })
    toast.value = `档案 ${secForm.docId} 密级已设为 ${CLASS_NAMES[secForm.classification]}`
    secForm.docId = ''
  } catch (e: any) {
    toast.value = e.message
  }
  setTimeout(() => (toast.value = ''), 2500)
}

onMounted(load)
</script>

<template>
  <div class="layout">
    <p v-if="err" class="card err">{{ err }}</p>
    <div class="card">
      <h3 style="margin-top: 0">👥 用户与角色</h3>
      <table>
        <thead>
          <tr><th>用户</th><th>部门</th><th>密级权限</th><th>角色</th><th>状态</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.id">
            <td><b>{{ u.username }}</b> <span class="muted">{{ u.display_name }}</span></td>
            <td>{{ u.dept || '-' }}</td>
            <td>
              <select :value="u.clearance" @change="setClearance(u, +($event.target as any).value)">
                <option v-for="(n, i) in CLASS_NAMES" :key="i" :value="i">{{ n }}</option>
              </select>
            </td>
            <td><span v-for="r in u.roles" :key="r" class="badge" style="margin-right: 4px">{{ r }}</span></td>
            <td>
              <span class="badge" :class="u.status === 'active' ? 'ok' : 'danger'">{{ u.status }}</span>
            </td>
            <td><button class="btn" @click="toggleStatus(u)">
              {{ u.status === 'active' ? '禁用' : '启用' }}</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="two-col">
      <div class="card">
        <h3 style="margin-top: 0">➕ 新建用户</h3>
        <div class="row" style="margin-bottom: 8px">
          <input v-model="form.username" placeholder="用户名" style="flex: 1" />
          <input v-model="form.password" placeholder="密码" type="password" style="flex: 1" />
        </div>
        <div class="row" style="margin-bottom: 8px">
          <input v-model="form.display_name" placeholder="姓名" style="flex: 1" />
          <input v-model="form.dept" placeholder="部门" style="flex: 1" />
        </div>
        <div class="row">
          <select v-model.number="form.clearance" style="flex: 1">
            <option v-for="(n, i) in CLASS_NAMES" :key="i" :value="i">可见密级: {{ n }}</option>
          </select>
          <select v-model="form.roles" style="flex: 1">
            <option value="viewer">viewer（只读）</option>
            <option value="archivist">archivist（档案员）</option>
            <option value="auditor">auditor（审计员）</option>
            <option value="admin">admin（管理员）</option>
          </select>
        </div>
        <button class="btn primary" style="margin-top: 10px; width: 100%" @click="createUser">
          创建用户
        </button>
      </div>
      <div class="card">
        <h3 style="margin-top: 0">🔒 档案密级设定</h3>
        <p class="muted" style="font-size: 13px">
          新入库档案默认「内部」。设为更高密级后，权限不足者检索、预览、下载全部被拒（fail-closed）。
        </p>
        <div class="row">
          <input v-model="secForm.docId" placeholder="档案 doc_id" style="width: 120px" />
          <select v-model.number="secForm.classification" style="width: 110px">
            <option v-for="(n, i) in CLASS_NAMES" :key="i" :value="i">{{ n }}</option>
          </select>
          <button class="btn primary" @click="setDocSecurity">设定</button>
        </div>
      </div>
    </div>
    <div v-if="toast" class="toast" :class="toast.includes('已') ? 'ok' : 'err'">{{ toast }}</div>
  </div>
</template>

<style scoped>
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 900px) { .two-col { grid-template-columns: 1fr; } }
.err { color: var(--danger); }
</style>
