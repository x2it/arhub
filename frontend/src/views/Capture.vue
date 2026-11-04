<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { api, authHeaders, hasRole } from '../api'

// ============ 对齐扫描全能王/微软 Lens：拍照或选图 → 服务端增强 → 批量归档 ============
//
// 采集入口分三种，缺一不可：
//   1) 拍照（capture=environment）：手机浏览器直接调起后置摄像头
//   2) 选择本机图片：桌面端从磁盘选，手机端从相册选（**不带 capture 属性**，
//      否则移动端会强制打开相机、用户无法访问已有的本机图片）
//   3) 拖拽：桌面端直接把文件拖进虚线框
//
// 服务端统一按魔数识别格式（不看扩展名），并做 EXIF 方向校正，
// 因此 HEIC / WebP / TIFF / BMP / GIF 与 JPG/PNG 一样可直接入库。

interface Shot {
  file: File
  preview: string
  enhancedPreview: string
  enhancedBlob: Blob | null
  mode: string
  blank: boolean
  status: 'enhancing' | 'ready' | 'failed'
  error: string
}

/** 可接受的文件类型：图片 + PDF（PDF 走扫描增强会失败，故单独区分） */
const ACCEPT = 'image/*,application/pdf,.heic,.heif,.pdf'
const IMAGE_EXT = /\.(jpe?g|png|webp|bmp|gif|tiff?|heic|heif)$/i

const shots = ref<Shot[]>([])
const mode = ref('enhance')
const busy = ref(false)
const uploading = ref(false)
const toast = ref('')
const toastOk = ref(true)
/** 拍照入口：带 capture，移动端强制调起相机 */
const cameraInput = ref<HTMLInputElement>()
/** 选择本机图片入口：不带 capture，移动端弹出相册/文件选择器 */
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
const readyCount = computed(() => shots.value.filter((s) => s.status === 'ready' && !s.blank).length)

function showToast(msg: string, ok = true) {
  toast.value = msg
  toastOk.value = ok
  setTimeout(() => (toast.value = ''), 3000)
}

/** 判断是否为 PDF —— PDF 不能做图像增强，直接原样入库。 */
function isPdf(file: File): boolean {
  if (file.type === 'application/pdf') return true
  return /\.pdf$/i.test(file.name)
}

/** 判断是否为可处理的图片。 */
function isImage(file: File): boolean {
  if (file.type.startsWith('image/')) return true
  if (isPdf(file)) return false
  return IMAGE_EXT.test(file.name)
}

async function addFiles(files: FileList | File[]) {
  const list = Array.from(files)
  const rejected: string[] = []
  for (const file of list) {
    if (!isImage(file) && !isPdf(file)) {
      rejected.push(file.name)
      continue
    }
    const shot: Shot = {
      file,
      preview: URL.createObjectURL(file),
      enhancedPreview: '',
      enhancedBlob: null,
      mode: mode.value,
      blank: false,
      status: 'enhancing',
      error: '',
    }
    shots.value.push(shot)
    const idx = shots.value.length - 1
    // PDF 不做图像增强，直接就绪
    if (isPdf(file)) {
      shot.status = 'ready'
      shot.mode = 'pdf'
    } else {
      enhanceOne(idx)
    }
  }
  if (rejected.length) {
    showToast(`已跳过 ${rejected.length} 个不支持的文件：${rejected.slice(0, 3).join('、')}`, false)
  }
}

async function enhanceOne(idx: number) {
  const shot = shots.value[idx]
  if (!shot) return
  shot.status = 'enhancing'
  shot.error = ''
  try {
    const fd = new FormData()
    fd.append('file', shot.file)
    fd.append('mode', mode.value)
    const res = await fetch('/api/v1/scan/enhance', {
      method: 'POST',
      headers: authHeaders(),
      body: fd,
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data?.detail || `增强失败 HTTP ${res.status}`)
    }
    const blob = await res.blob()
    shot.enhancedBlob = blob
    shot.enhancedPreview = URL.createObjectURL(blob)
    shot.mode = mode.value
    shot.blank = res.headers.get('X-Blank-Page') === '1'
    shot.status = 'ready'
  } catch (e: any) {
    shot.status = 'failed'
    shot.error = e.message || String(e)
    showToast(`${shot.file.name}：${shot.error}`, false)
  }
}

async function reEnhanceAll() {
  busy.value = true
  for (let i = 0; i < shots.value.length; i++) {
    if (isPdf(shots.value[i].file)) continue
    await enhanceOne(i)
  }
  busy.value = false
}

function removeShot(idx: number) {
  const s = shots.value[idx]
  if (s) {
    if (s.preview) URL.revokeObjectURL(s.preview)
    if (s.enhancedPreview) URL.revokeObjectURL(s.enhancedPreview)
  }
  shots.value.splice(idx, 1)
}

function onPick(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files) addFiles(input.files)
  // 重置 value，保证连续选择同一文件也能再次触发 change
  input.value = ''
}

function onDrop(e: DragEvent) {
  if (e.dataTransfer?.files) addFiles(e.dataTransfer.files)
}

/** 扩展名映射：增强产物一律是 JPEG，原图则保留真实类型 */
function extOf(file: File): string {
  const m = /\.([a-z0-9]+)$/i.exec(file.name)
  if (m) return m[1].toLowerCase()
  if (file.type === 'image/png') return 'png'
  if (file.type === 'application/pdf') return 'pdf'
  return 'jpg'
}

async function uploadAll() {
  if (!canUpload.value) {
    showToast('需要档案员或管理员权限', false)
    return
  }
  if (!shots.value.length) return
  uploading.value = true
  let okCount = 0
  let skipCount = 0
  const failed: string[] = []
  for (const shot of shots.value) {
    if (shot.blank) {
      skipCount++
      continue
    }
    if (shot.status === 'failed') {
      failed.push(shot.file.name)
      continue
    }
    const fd = new FormData()
    // 归档优先用增强件；无则用原图（PDF 原样提交，由 Paperless 处理）
    const payload = shot.enhancedBlob || shot.file
    const base = shot.file.name.replace(/\.[^.]+$/, '') || 'document'
    const name = shot.enhancedBlob ? `${base}.jpg` : `${base}.${extOf(shot.file)}`
    fd.append('file', payload, name)
    fd.append('title', form.title || base)
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
      failed.push(`${shot.file.name}（${e.message}）`)
    }
  }
  uploading.value = false
  if (failed.length) {
    showToast(`已提交 ${okCount} 份，${failed.length} 份失败：${failed[0]}`, false)
  } else {
    const extra = skipCount ? `，自动跳过 ${skipCount} 张空白页` : ''
    showToast(`已提交 ${okCount} 份${extra}，OCR 完成后自动可检索`)
  }
  if (okCount > 0) shots.value = []
}
</script>

<template>
  <div class="layout">
    <div class="card">
      <h3 style="margin-top: 0">
        📷 扫描采集
        <span class="muted" style="font-weight: 400"
          >· 对齐扫描全能王：自动裁边 / 透视校正 / 去阴影 / 增强</span
        >
      </h3>

      <!-- 两个独立入口：拍照（capture）与选择本机图片（无 capture） -->
      <div class="entry-row">
        <button class="btn primary" @click="cameraInput?.click()">📸 拍照扫描</button>
        <button class="btn" @click="fileInput?.click()">🖼️ 选择本机图片</button>
        <span class="muted">支持 JPG / PNG / WebP / HEIC / TIFF / BMP / GIF / PDF，可多选</span>
      </div>

      <input
        ref="cameraInput"
        type="file"
        accept="image/*"
        capture="environment"
        multiple
        style="display: none"
        @change="onPick"
      />
      <input ref="fileInput" type="file" :accept="ACCEPT" multiple style="display: none" @change="onPick" />

      <div class="drop" @dragover.prevent @drop.prevent="onDrop" @click="fileInput?.click()">
        <p v-if="!shots.length">
          🖼️ 点击选择本机图片，或把文件拖拽到此处<br />
          <span class="muted">手机上可从相册选取已拍照片，桌面端从磁盘选取</span>
        </p>
        <p v-else class="muted">
          点击继续添加（已选 {{ shots.length }} 个文件，其中 {{ readyCount }} 份可归档）
        </p>
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
        <button class="btn danger" :disabled="!shots.length" @click="shots = []">清空</button>
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
        <div class="shot-head">
          <span class="fname" :title="s.file.name">{{ s.file.name }}</span>
          <span v-if="s.status === 'enhancing'" class="spinner"></span>
          <span v-else-if="s.status === 'failed'" class="tag err">增强失败</span>
          <span v-else-if="s.blank" class="tag warn">空白页·自动跳过</span>
          <span v-else class="tag ok">就绪</span>
        </div>
        <div v-if="s.mode === 'pdf'" class="pdf-box">📄 PDF 文档（直接入库，不做图像增强）</div>
        <div v-else class="pair">
          <figure>
            <img :src="s.preview" />
            <figcaption class="muted">原图</figcaption>
          </figure>
          <figure>
            <img v-if="s.enhancedPreview" :src="s.enhancedPreview" />
            <div v-else-if="s.status === 'failed'" class="err-box">⚠ {{ s.error }}</div>
            <div v-else class="spinner" style="margin: 60px auto"></div>
            <figcaption class="muted">增强后（{{ s.mode }}）</figcaption>
          </figure>
        </div>
        <div class="row">
          <button class="btn" :disabled="s.mode === 'pdf'" @click="enhanceOne(i)">重新增强</button>
          <button class="btn danger" @click="removeShot(i)">删除本页</button>
        </div>
      </div>
    </div>

    <div v-if="shots.length" class="card row" style="justify-content: flex-end">
      <span class="muted">增强完成后进入 OCR → 档号著录 → 检索索引，全自动</span>
      <button class="btn primary" :disabled="uploading || busy || !readyCount" @click="uploadAll">
        <span v-if="uploading" class="spinner" style="border-top-color: #fff"></span>
        批量归档上传（{{ readyCount }} 份可归档）
      </button>
    </div>

    <div v-if="toast" class="toast" :class="toastOk ? 'ok' : 'err'">{{ toast }}</div>
  </div>
</template>

<style scoped>
.entry-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.drop {
  border: 2px dashed var(--border);
  border-radius: var(--radius);
  padding: 28px;
  text-align: center;
  cursor: pointer;
  color: var(--muted);
}
.drop:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(460px, 1fr));
  gap: 14px;
}
.shot-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.fname {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 260px;
}
.tag {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid var(--border);
  white-space: nowrap;
}
.tag.ok {
  color: var(--ok);
  border-color: var(--ok);
}
.tag.warn {
  color: var(--warn);
  border-color: var(--warn);
}
.tag.err {
  color: var(--danger);
  border-color: var(--danger);
}
.pdf-box {
  padding: 28px 12px;
  text-align: center;
  background: var(--accent-weak);
  border-radius: 6px;
  color: var(--accent);
}
.err-box {
  padding: 12px;
  color: var(--danger);
  font-size: 12px;
  text-align: center;
  background: #fef2f2;
  border-radius: 6px;
}
.pair {
  display: flex;
  gap: 10px;
}
.pair figure {
  flex: 1;
  margin: 0 0 8px;
}
.pair img {
  width: 100%;
  height: 220px;
  object-fit: contain;
  background: #f0f0f0;
  border-radius: 6px;
}
figcaption {
  font-size: 12px;
  text-align: center;
  margin-top: 4px;
}
</style>
