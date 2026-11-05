<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { AuthError, authFetch, getUser, readError } from '../api'

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
// 增强服务异常时自动降级为「原图归档」，主流程不因单点失败被阻塞。

interface Shot {
  file: File
  preview: string
  enhancedPreview: string
  enhancedBlob: Blob | null
  mode: string
  blank: boolean
  /** 服务端图像质量判定：ok / low-contrast（疑似翻拍屏幕或文字过淡） */
  quality: string
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
let toastTimer: ReturnType<typeof setTimeout> | undefined
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

/** 每次提交时实时读取用户角色，不用 computed 缓存（切换账号后不会残留旧权限）。 */
function canUploadNow(): boolean {
  const u = getUser()
  return !!u && (u.roles || []).some((r: string) => ['archivist', 'admin'].includes(r))
}
/** 可归档页数：就绪页 + 增强失败但可按原图归档的页（自动降级） */
const archivableCount = computed(
  () => shots.value.filter((s) => !s.blank && s.status !== 'enhancing').length,
)
const degradedCount = computed(() => shots.value.filter((s) => s.status === 'failed').length)
const lowQualityCount = computed(
  () => shots.value.filter((s) => !s.blank && s.status === 'ready' && s.quality === 'low-contrast').length,
)

function showToast(msg: string, ok = true) {
  if (toastTimer) clearTimeout(toastTimer)
  toast.value = msg
  toastOk.value = ok
  toastTimer = setTimeout(() => (toast.value = ''), 4000)
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
      quality: 'ok',
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
    const res = await authFetch('/api/v1/scan/enhance', {
      method: 'POST',
      body: fd,
    })
    if (!res.ok) throw new Error(await readError(res))
    const blob = await res.blob()
    shot.enhancedBlob = blob
    shot.enhancedPreview = URL.createObjectURL(blob)
    shot.mode = mode.value
    shot.blank = res.headers.get('X-Blank-Page') === '1'
    shot.quality = res.headers.get('X-Image-Quality') || 'ok'
    shot.status = 'ready'
  } catch (e: any) {
    // 登录态失效已由 authFetch 统一登出并跳登录页，不再做业务降级提示
    if (e instanceof AuthError) return
    // 增强失败不阻塞归档：保留原图，由 uploadAll 自动降级提交
    shot.status = 'failed'
    shot.error = e.message || String(e)
    showToast(`${shot.file.name}：${shot.error}（将按原图归档）`, false)
  }
}

async function reEnhanceAll() {
  busy.value = true
  try {
    for (let i = 0; i < shots.value.length; i++) {
      if (isPdf(shots.value[i].file)) continue
      await enhanceOne(i)
    }
  } finally {
    busy.value = false
  }
}

function removeShot(idx: number) {
  const s = shots.value[idx]
  if (s) {
    if (s.preview) URL.revokeObjectURL(s.preview)
    if (s.enhancedPreview) URL.revokeObjectURL(s.enhancedPreview)
  }
  shots.value.splice(idx, 1)
}

function clearAll() {
  shots.value.forEach((s) => {
    if (s.preview) URL.revokeObjectURL(s.preview)
    if (s.enhancedPreview) URL.revokeObjectURL(s.enhancedPreview)
  })
  shots.value = []
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

/** 手机相册导出的文件名常是纯数字时间戳，直接当题名会让档案列表出现一串
 *  无法辨认的数字；OCR 为空时更难定位是哪份档案。改为可读的「扫描件 + 本地时间」。 */
function readableTitle(base: string): string {
  if (base && !/^[\d_\-]{6,}$/.test(base)) return base
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `扫描件 ${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
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
  if (!canUploadNow()) {
    showToast('需要档案员或管理员权限', false)
    return
  }
  if (!archivableCount.value) return
  uploading.value = true
  let okCount = 0
  let skipCount = 0
  const failed: string[] = []
  for (const shot of shots.value) {
    if (shot.blank || shot.status === 'enhancing') {
      if (shot.blank) skipCount++
      continue
    }
    const fd = new FormData()
    // 归档优先用增强件；增强失败或 PDF 则用原图（自动降级，保证主流程可用）
    const degraded = shot.status === 'failed' || !shot.enhancedBlob
    const payload = degraded ? shot.file : shot.enhancedBlob!
    const base = shot.file.name.replace(/\.[^.]+$/, '') || 'document'
    const name = degraded ? `${base}.${extOf(shot.file)}` : `${base}.jpg`
    fd.append('file', payload, name)
    fd.append('title', form.title || readableTitle(base))
    fd.append('created', form.created)
    if (form.asn) fd.append('asn', form.asn)
    if (form.correspondent) fd.append('correspondent', form.correspondent)
    if (form.document_type) fd.append('document_type', form.document_type)
    if (form.tags) fd.append('tags', form.tags)
    fd.append('classification', String(form.classification))
    try {
      const res = await authFetch('/api/v1/upload', {
        method: 'POST',
        body: fd,
      })
      if (!res.ok) throw new Error(await readError(res))
      okCount++
    } catch (e: any) {
      if (e instanceof AuthError) {
        uploading.value = false
        return
      }
      failed.push(`${shot.file.name}（${e.message}）`)
    }
  }
  uploading.value = false
  if (failed.length) {
    showToast(`已提交 ${okCount} 份，${failed.length} 份失败：${failed[0]}`, false)
  } else {
    const extra = skipCount ? `，自动跳过 ${skipCount} 张空白页` : ''
    const warn = lowQualityCount.value
      ? `；其中 ${lowQualityCount.value} 张文字偏淡，OCR 可能识别不出，可在检索页查看标注`
      : ''
    showToast(`已提交 ${okCount} 份${extra}，OCR 完成后自动可检索${warn}`, !lowQualityCount.value)
  }
  if (okCount > 0) clearAll()
}
</script>

<template>
  <div class="layout capture">
    <!-- ===== 1. 采集入口 ===== -->
    <section class="card">
      <header class="card-head">
        <div class="head-text">
          <h3>📷 扫描采集</h3>
          <p class="muted">对齐扫描全能王 / 微软 Lens：自动裁边 · 透视校正 · 去阴影 · 增强</p>
        </div>
        <span class="badge">本地选图 · 服务端处理</span>
      </header>

      <div class="entry-grid">
        <button class="entry" @click="cameraInput?.click()">
          <span class="entry-icon">📸</span>
          <span class="entry-body">
            <b>拍照扫描</b>
            <small class="muted">调起后置摄像头</small>
          </span>
        </button>
        <button class="entry" @click="fileInput?.click()">
          <span class="entry-icon">🖼️</span>
          <span class="entry-body">
            <b>选择本机图片</b>
            <small class="muted">相册 / 磁盘 · 可多选</small>
          </span>
        </button>
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

      <p class="format-hint">
        支持 <b>JPG / PNG / WebP / HEIC / TIFF / BMP / GIF / PDF</b>，可多选、可拖拽；自动按 EXIF 校正方向、展开多页 TIFF。
      </p>

      <div class="drop" @dragover.prevent @drop.prevent="onDrop" @click="fileInput?.click()">
        <template v-if="!shots.length">
          <div class="drop-icon">🖼️</div>
          <p class="drop-title">点击选择本机图片，或把文件拖拽到此处</p>
          <p class="muted drop-sub">手机上可从相册选取已拍照片，桌面端从磁盘选取</p>
        </template>
        <p v-else class="drop-title muted">
          点击继续添加（已选 {{ shots.length }} 个文件，其中 {{ archivableCount }} 份可归档）
        </p>
      </div>
    </section>

    <!-- ===== 2. 增强设置 ===== -->
    <section v-if="shots.length" class="card">
      <header class="card-head">
        <div class="head-text">
          <h3>🎛 图像增强</h3>
          <p class="muted">共 {{ shots.length }} 页 · 服务端 OpenCV 处理</p>
        </div>
        <span v-if="degradedCount" class="badge warn">{{ degradedCount }} 页将按原图归档</span>
      </header>
      <div class="settings-row">
        <label class="field mode-field">
          <span>增强模式</span>
          <select v-model="mode" @change="shots.length && reEnhanceAll()">
            <option value="enhance">增强（去阴影 + 对比度）</option>
            <option value="gray">灰度文档</option>
            <option value="bw">黑白二值</option>
            <option value="orig">仅裁边校正</option>
          </select>
        </label>
        <div class="settings-actions">
          <button class="btn" :disabled="busy || !shots.length" @click="reEnhanceAll">
            <span v-if="busy" class="spinner"></span> 重新增强全部
          </button>
          <button class="btn danger-ghost" :disabled="!shots.length" @click="clearAll">清空</button>
        </div>
      </div>
      <p v-if="lowQualityCount" class="quality-warn">
        ⚠ 检测到 {{ lowQualityCount }} 张图片<b>文字偏淡</b>（常见于翻拍电脑屏幕、光线不足），
        OCR 可能识别不出内容。已自动改用温和增强保护笔画；建议重拍纸质原件，
        或把增强模式切到「仅裁边校正」后重新增强。
      </p>
    </section>

    <!-- ===== 3. 著录信息 ===== -->
    <section v-if="shots.length" class="card">
      <header class="card-head">
        <div class="head-text">
          <h3>🗂 著录信息</h3>
          <p class="muted">随档案一并提交；题名留空则取文件名，档号留空自动生成</p>
        </div>
      </header>
      <div class="meta-grid">
        <label class="field span-2">
          <span>题名</span>
          <input v-model="form.title" placeholder="空则用文件名" />
        </label>
        <label class="field">
          <span>成文日期</span>
          <input v-model="form.created" type="date" />
        </label>
        <label class="field">
          <span>档号</span>
          <input v-model="form.asn" placeholder="自动生成" />
        </label>
        <label class="field">
          <span>责任者</span>
          <input v-model="form.correspondent" placeholder="责任者" />
        </label>
        <label class="field">
          <span>门类</span>
          <input v-model="form.document_type" placeholder="通知" />
        </label>
        <label class="field">
          <span>标签</span>
          <input v-model="form.tags" placeholder="逗号分隔" />
        </label>
        <label class="field">
          <span>密级</span>
          <select v-model.number="form.classification">
            <option :value="0">公开</option>
            <option :value="1">内部</option>
            <option :value="2">秘密</option>
            <option :value="3">机密</option>
          </select>
        </label>
      </div>
    </section>

    <!-- ===== 4. 页面预览 ===== -->
    <section v-if="shots.length" class="shot-grid">
      <article v-for="(s, i) in shots" :key="i" class="card shot-card">
        <div class="shot-head">
          <span class="fname" :title="s.file.name">{{ s.file.name }}</span>
          <span v-if="s.status === 'enhancing'" class="tag"><span class="spinner"></span> 处理中</span>
          <span v-else-if="s.status === 'failed'" class="tag err">增强失败</span>
          <span v-else-if="s.blank" class="tag warn">空白页 · 自动跳过</span>
          <span v-else class="tag ok">就绪</span>
        </div>

        <div v-if="s.mode === 'pdf'" class="pdf-box">📄 PDF 文档 · 直接入库，不做图像增强</div>
        <div v-else class="pair">
          <figure>
            <img :src="s.preview" alt="原图" />
            <figcaption class="muted">原图</figcaption>
          </figure>
          <figure>
            <img v-if="s.enhancedPreview" :src="s.enhancedPreview" alt="增强后" />
            <div v-else-if="s.status === 'failed'" class="err-box">⚠ {{ s.error }}</div>
            <div v-else class="spinner spinner-center"></div>
            <figcaption class="muted">增强后（{{ s.mode }}）</figcaption>
          </figure>
        </div>

        <p v-if="s.quality === 'low-contrast' && !s.blank && s.status === 'ready'" class="quality-note">
          ⚠ 文字偏淡，OCR 可能识别不出内容，建议重拍
        </p>
        <p v-if="s.status === 'failed'" class="fallback-note">
          ⚠ 增强服务不可用，将按<b>原图</b>归档；可稍后重试增强。
        </p>
        <div class="shot-actions">
          <button class="btn" :disabled="s.mode === 'pdf'" @click="enhanceOne(i)">重新增强</button>
          <button class="btn danger-ghost" @click="removeShot(i)">删除本页</button>
        </div>
      </article>
    </section>

    <!-- ===== 5. 提交 ===== -->
    <section v-if="shots.length" class="card submit-bar">
      <div class="submit-hint">
        <b>{{ archivableCount }} 份可归档</b>
        <span class="muted">增强完成后自动进入 OCR → 档号著录 → 检索索引</span>
      </div>
      <button class="btn primary submit-btn" :disabled="uploading || busy || !archivableCount" @click="uploadAll">
        <span v-if="uploading" class="spinner" style="border-top-color: #fff"></span>
        批量归档上传（{{ archivableCount }}）
      </button>
    </section>

    <div v-if="toast" class="toast" :class="toastOk ? 'ok' : 'err'" role="status">
      <span class="toast-text">{{ toast }}</span>
      <button class="toast-close" aria-label="关闭提示" @click="toast = ''">×</button>
    </div>
  </div>
</template>

<style scoped>
/* ---------- 通用卡片头 ---------- */
.card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}
.head-text { min-width: 0; }
.card-head h3 { margin: 0 0 4px; font-size: 16px; }
.card-head p { margin: 0; font-size: 12px; line-height: 1.5; }
.card-head .badge { flex: none; font-size: 11px; }

/* ---------- 采集入口 ---------- */
.entry-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr));
  gap: 10px;
}
.entry {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 68px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: linear-gradient(180deg, #fff, #fbfdff);
  cursor: pointer;
  text-align: left;
  transition: border-color .15s, box-shadow .15s, transform .15s;
}
.entry:hover { border-color: var(--accent); box-shadow: 0 4px 16px rgba(14, 107, 168, .12); transform: translateY(-1px); }
.entry:active { transform: translateY(0); }
.entry-icon { font-size: 24px; flex: none; }
.entry-body { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.entry-body b { font-size: 14px; font-weight: 600; color: var(--text); }
.entry-body small { font-size: 11px; }
.format-hint {
  margin: 12px 0;
  font-size: 12px;
  line-height: 1.7;
  color: var(--muted);
  word-break: break-word;
}
.format-hint b { color: var(--ink-2); font-weight: 600; }
.drop {
  border: 2px dashed var(--border);
  border-radius: var(--radius);
  padding: 22px 14px;
  text-align: center;
  cursor: pointer;
  color: var(--muted);
  transition: border-color .15s, background .15s;
}
.drop:hover { border-color: var(--accent); background: var(--accent-weak); color: var(--accent); }
.drop-icon { font-size: 26px; margin-bottom: 6px; }
.drop-title { margin: 0 0 6px; font-size: 13px; }
.drop-sub { margin: 0; font-size: 12px; }

/* ---------- 表单 ---------- */
.settings-row { display: flex; align-items: flex-end; gap: 12px; flex-wrap: wrap; }
.settings-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.field > span { font-size: 12px; color: var(--muted); }
.field input, .field select { width: 100%; min-width: 0; }
.mode-field { flex: 0 1 240px; }
.meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.span-2 { grid-column: 1 / -1; }
.btn.danger-ghost {
  background: #fff;
  color: var(--danger);
  border-color: #f3c2c2;
}
.btn.danger-ghost:hover { background: #fef2f2; }

/* ---------- 预览卡片 ---------- */
.shot-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(460px, 100%), 1fr));
  gap: 14px;
}
.shot-card { margin-bottom: 0; }
.shot-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  min-width: 0;
}
.fname {
  flex: 1 1 auto;
  min-width: 0;
  max-width: 100%;
  font-weight: 600;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tag {
  flex: none;
  font-size: 11px;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid var(--border);
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.tag.ok { color: var(--ok); border-color: var(--ok); background: #f0fdf4; }
.tag.warn { color: var(--warn); border-color: var(--warn); background: #fffbeb; }
.tag.err { color: var(--danger); border-color: var(--danger); background: #fef2f2; }
.pdf-box {
  padding: 32px 12px;
  text-align: center;
  background: var(--accent-weak);
  border-radius: 8px;
  color: var(--accent);
  font-size: 13px;
}
.err-box {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 220px;
  padding: 12px;
  color: var(--danger);
  font-size: 12px;
  line-height: 1.6;
  text-align: center;
  background: #fef2f2;
  border-radius: 8px;
  word-break: break-word;
}
.spinner-center { margin: 100px auto; }
.pair { display: flex; gap: 10px; }
.pair figure { flex: 1 1 0; min-width: 0; margin: 0 0 8px; }
.pair img {
  width: 100%;
  height: 220px;
  object-fit: contain;
  background: #f0f3f7;
  border-radius: 8px;
}
figcaption { font-size: 12px; text-align: center; margin-top: 6px; }
.fallback-note {
  margin: 4px 0 12px;
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.6;
  color: #92400e;
  background: #fffbeb;
  border-radius: 8px;
}
.quality-warn {
  margin: 12px 0 0;
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.75;
  color: #92400e;
  background: #fffbeb;
  border-left: 3px solid var(--warn);
  border-radius: 6px;
  word-break: break-word;
}
.quality-note {
  margin: 0 0 10px;
  padding: 6px 9px;
  font-size: 12px;
  line-height: 1.6;
  color: #92400e;
  background: #fffbeb;
  border-radius: 6px;
}
.shot-actions { display: flex; gap: 8px; flex-wrap: wrap; }

/* ---------- 提交条 ---------- */
.submit-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.submit-hint { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.submit-hint b { font-size: 14px; }
.submit-hint .muted { font-size: 12px; }
.submit-btn { padding: 10px 20px; font-size: 14px; font-weight: 600; }

/* ---------- 移动端适配 ---------- */
@media (max-width: 640px) {
  .card-head { margin-bottom: 12px; }
  .card-head .badge { display: none; }
  .entry-grid { grid-template-columns: 1fr 1fr; gap: 8px; }
  .entry { min-height: 62px; padding: 10px; gap: 8px; }
  .entry-icon { font-size: 20px; }
  .entry-body b { font-size: 13px; }
  .entry-body small { font-size: 10px; }
  .drop { padding: 16px 10px; }
  .settings-row { align-items: stretch; }
  .mode-field { flex: 1 1 100%; }
  .settings-actions { width: 100%; }
  .settings-actions .btn { flex: 1; }
  .meta-grid { grid-template-columns: 1fr; gap: 10px; }
  .span-2 { grid-column: auto; }
  /* 手机上表单改为「标签 + 输入框」横向排布，避免 6 个字段纵向拉出很长的滚动 */
  .field { flex-direction: row; align-items: center; gap: 8px; }
  .field > span { flex: 0 0 66px; font-size: 12px; }
  .field input, .field select { flex: 1 1 auto; min-width: 0; }
  .shot-grid { grid-template-columns: 1fr; gap: 12px; }
  .pair img { height: 180px; }
  .err-box { min-height: 180px; }
  .spinner-center { margin: 80px auto; }
  .shot-actions .btn { flex: 1; }
  .submit-bar { flex-direction: column; align-items: stretch; }
  .submit-hint { text-align: left; }
  .submit-btn { width: 100%; min-height: 44px; }
}
/* 常规手机（含 375/390/414px）：入口保持双列并列，对比图改为上下以便看清细节 */
@media (max-width: 420px) {
  .pair { flex-direction: column; }
  .pair img { height: 200px; }
  .err-box { min-height: 140px; }
}
/* 极窄屏才把入口改为单列，避免 375px 主流机型白白多占一行 */
@media (max-width: 340px) {
  .entry-grid { grid-template-columns: 1fr; }
}
</style>
