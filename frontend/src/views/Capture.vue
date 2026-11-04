<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { api, authHeaders, hasRole } from '../api'

// ============ 对齐扫描全能王/微软 Lens：拍照 → 服务端增强 → 批量归档 ============
interface Shot {
  file: File
  preview: string
  enhancedPreview: string
  enhancedBlob: Blob | null
  mode: string
  blank: boolean
}

const shots = ref<Shot[]>([])
const mode = ref('enhance')
const busy = ref(false)
const uploading = ref(false)
const toast = ref('')
const toastOk = ref(true)
const fileInput = ref<HTMLInputElement>()

const form = reactive({
  title: '',
  created: new Date().toISOString().slice(0, 10),
  asn: '',
  correspondent: '',
  document_type: '通知',
  tags: '',
  classification: 1,
})

const canUpload = computed(() => hasRole('archivist', 'admin'))

function showToast(msg: string, ok = true) {
  toast.value = msg
  toastOk.value = ok
  setTimeout(() => (toast.value = ''), 3000)
}

async function addFiles(files: FileList | File[]) {
  for (const file of Array.from(files)) {
    if (!file.type.startsWith('image/')) continue
    const shot: Shot = {
      file,
      preview: URL.createObjectURL(file),
      enhancedPreview: '',
      enhancedBlob: null,
      mode: mode.value,
      blank: false,
    }
    shots.value.push(shot)
    enhanceOne(shots.value.length - 1)
  }
}

async function enhanceOne(idx: number) {
  const shot = shots.value[idx]
  if (!shot) return
  busy.value = true
  try {
    const fd = new FormData()
    fd.append('file', shot.file)
    fd.append('mode', mode.value)
    const res = await fetch('/api/v1/scan/enhance', {
      method: 'POST',
      headers: authHeaders(),
      body: fd,
    })
    if (!res.ok) throw new Error(`增强失败 HTTP ${res.status}`)
    const blob = await res.blob()
    shot.enhancedBlob = blob
    shot.enhancedPreview = URL.createObjectURL(blob)
    shot.mode = mode.value
  } catch (e: any) {
    showToast(e.message, false)
  } finally {
    busy.value = false
  }
}

async function reEnhanceAll() {
  for (let i = 0; i < shots.value.length; i++) await enhanceOne(i)
}

function removeShot(idx: number) {
  shots.value.splice(idx, 1)
}

function onPick(e: Event) {
  const files = (e.target as HTMLInputElement).files
  if (files) addFiles(files)
}

function onDrop(e: DragEvent) {
  if (e.dataTransfer?.files) addFiles(e.dataTransfer.files)
}

// 多页合并为单个 PDF（用服务端逐页 JPEG → 前端合成？）
// 简化策略：逐张上传为独立档案（档号自动），或仅第一页+著录信息
async function uploadAll() {
  if (!canUpload.value) {
    showToast('需要档案员或管理员权限', false)
    return
  }
  uploading.value = true
  let okCount = 0
  for (const shot of shots.value) {
    if (shot.blank) continue
    const fd = new FormData()
    // 归档优先用增强件；无则用原图转 PDF 由 Paperless 处理（支持图片格式）
    const payload = shot.enhancedBlob || shot.file
    fd.append('file', payload, shot.file.name.replace(/\.\w+$/, '') + '.jpg')
    fd.append('title', form.title || shot.file.name.replace(/\.\w+$/, ''))
    fd.append('created', form.created)
    if (form.asn) fd.append('asn', form.asn)
    if (form.correspondent) fd.append('correspondent', form.correspondent)
    if (form.document_type) fd.append('document_type', form.document_type)
    if (form.tags) fd.append('tags', form.tags)
    fd.append('classification', String(form.classification))
    try {
      const res = await fetch('/api/v1/upload', {
        method: 'POST',
        headers: authHeaders(),
        body: fd,
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data?.detail || `上传失败 HTTP ${res.status}`)
      }
      okCount++
    } catch (e: any) {
      showToast(e.message, false)
    }
  }
  uploading.value = false
  showToast(`已提交 ${okCount}/${shots.value.length} 份，OCR 完成后自动可检索`)
  shots.value = []
}
</script>

<template>
  <div class="layout">
    <div class="card">
      <h3 style="margin-top: 0">📷 扫描采集 <span class="muted" style="font-weight: 400"
        >· 对齐扫描全能王：自动裁边 / 透视校正 / 去阴影 / 增强</span
      ></h3>
      <div
        class="drop"
        @dragover.prevent
        @drop.prevent="onDrop"
        @click="fileInput?.click()"
      >
        <input
          ref="fileInput"
          type="file"
          accept="image/*"
          capture="environment"
          multiple
          style="display: none"
          @change="onPick"
        />
        <p v-if="!shots.length">
          📱 点击拍照 / 选择图片，或拖拽到此处<br />
          <span class="muted">手机浏览器可直接调起相机连续拍摄</span>
        </p>
        <p v-else class="muted">点击继续添加页面（{{ shots.length }} 页）</p>
      </div>
      <div class="row" style="margin-top: 12px">
        <b>增强模式:</b>
        <select v-model="mode" style="width: 200px" @change="shots.length && reEnhanceAll()">
          <option value="enhance">增强（去阴影+对比度）</option>
          <option value="gray">灰度文档</option>
          <option value="bw">黑白二值</option>
          <option value="orig">仅裁边校正</option>
        </select>
        <button class="btn" :disabled="busy || !shots.length" @click="reEnhanceAll">
          <span v-if="busy" class="spinner"></span> 重新增强全部
        </button>
      </div>
    </div>

    <div v-if="shots.length" class="card">
      <h3 style="margin-top: 0">🗂 著录信息（随档案提交）</h3>
      <div class="row">
        <input v-model="form.title" placeholder="题名（空则用文件名）" style="flex: 1; min-width: 200px" />
        <input v-model="form.created" type="date" />
        <input v-model="form.asn" placeholder="档号(自动)" style="width: 110px" />
      </div>
      <div class="row" style="margin-top: 8px">
        <input v-model="form.correspondent" placeholder="责任者" style="width: 140px" />
        <input v-model="form.document_type" placeholder="门类" style="width: 120px" />
        <input v-model="form.tags" placeholder="标签(逗号分隔)" style="width: 180px" />
        <select v-model.number="form.classification" style="width: 100px">
          <option :value="0">公开</option>
          <option :value="1">内部</option>
          <option :value="2">秘密</option>
          <option :value="3">机密</option>
        </select>
      </div>
    </div>

    <div v-if="shots.length" class="grid">
      <div v-for="(s, i) in shots" :key="i" class="card shot-card">
        <div class="pair">
          <figure>
            <img :src="s.preview" />
            <figcaption class="muted">原图</figcaption>
          </figure>
          <figure>
            <img v-if="s.enhancedPreview" :src="s.enhancedPreview" />
            <div v-else class="spinner" style="margin: 60px auto"></div>
            <figcaption class="muted">增强后（{{ s.mode }}）</figcaption>
          </figure>
        </div>
        <div class="row">
          <button class="btn" @click="enhanceOne(i)">重新增强</button>
          <button class="btn danger" @click="removeShot(i)">删除本页</button>
        </div>
      </div>
    </div>

    <div v-if="shots.length" class="card row" style="justify-content: flex-end">
      <span class="muted">增强完成后进入 OCR → 档号著录 → 检索索引，全自动</span>
      <button class="btn primary" :disabled="uploading || busy" @click="uploadAll">
        <span v-if="uploading" class="spinner" style="border-top-color: #fff"></span>
        批量归档上传（{{ shots.length }} 页）
      </button>
    </div>

    <div v-if="toast" class="toast" :class="toastOk ? 'ok' : 'err'">{{ toast }}</div>
  </div>
</template>

<style scoped>
.drop {
  border: 2px dashed var(--border);
  border-radius: var(--radius);
  padding: 36px;
  text-align: center;
  cursor: pointer;
  color: var(--muted);
}
.drop:hover { border-color: var(--accent); color: var(--accent); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(460px, 1fr)); gap: 14px; }
.pair { display: flex; gap: 10px; }
.pair figure { flex: 1; margin: 0 0 8px; }
.pair img { width: 100%; height: 220px; object-fit: contain; background: #f0f0f0; border-radius: 6px; }
figcaption { font-size: 12px; text-align: center; margin-top: 4px; }
</style>
