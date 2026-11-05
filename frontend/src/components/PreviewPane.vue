<script setup lang="ts">
/** 通用在线预览画廊。
 *
 * 关键设计：后端把任意原件/归档版渲染成 JPEG 页，前端只用 <img> 展示，
 * 彻底不依赖浏览器内置 PDF 阅读器（移动端 / Edge / 微信 WebView 常禁用它）。
 * 图片走 authFetch（三通道 JWT + 401 统一登出）+ blob URL，不用裸 <img src>，
 * 避免公网反代下 cookie 单通道失效。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { authFetch, downloadFile, readError } from '../api'

const props = defineProps<{ docId: string | number }>()

const page = ref(1)
const pageCount = ref(1)
const src = ref<'archive' | 'original'>('archive')
const imgUrl = ref('')
const loading = ref(false)
const err = ref('')
const originalMime = ref('')
const zoomed = ref(false)
const scale = ref(1)
const pageInput = ref('1')

let objectUrl = ''

async function load() {
  loading.value = true
  err.value = ''
  try {
    const res = await authFetch(
      `/api/v1/documents/${props.docId}/preview?page=${page.value}&src=${src.value}`)
    if (!res.ok) throw new Error(await readError(res))
    pageCount.value = Math.max(1, Number(res.headers.get('X-Page-Count') || 1))
    originalMime.value = res.headers.get('X-Original-Mime') || ''
    pageInput.value = String(page.value)
    const blob = await res.blob()
    if (objectUrl) URL.revokeObjectURL(objectUrl)
    objectUrl = URL.createObjectURL(blob)
    imgUrl.value = objectUrl
  } catch (e: any) {
    err.value = e.message
  } finally {
    loading.value = false
  }
}

function goto(p: number) {
  const next = Math.min(Math.max(1, p), pageCount.value)
  if (next === page.value) return
  page.value = next
  scale.value = 1
  load()
}

function jump() {
  const n = parseInt(pageInput.value, 10)
  if (Number.isFinite(n)) goto(n)
  else pageInput.value = String(page.value)
}

function switchSrc(s: 'archive' | 'original') {
  if (src.value === s) return
  src.value = s
  page.value = 1
  scale.value = 1
  load()
}

async function doDownload() {
  const original = src.value === 'original'
  try {
    await downloadFile(
      `/api/v1/documents/${props.docId}/download?original=${original}`,
      `arhub-${props.docId}-${original ? 'original' : 'archive'}.${original ? 'jpg' : 'pdf'}`)
  } catch (e: any) {
    err.value = e.message
  }
}

function openZoom() {
  zoomed.value = true
  scale.value = 1
}
function closeZoom() {
  zoomed.value = false
  scale.value = 1
}
function bump(delta: number) {
  scale.value = Math.min(3, Math.max(0.6, +(scale.value + delta).toFixed(2)))
}

onMounted(load)
onBeforeUnmount(() => { if (objectUrl) URL.revokeObjectURL(objectUrl) })
watch(() => props.docId, () => { page.value = 1; scale.value = 1; load() })
</script>

<template>
  <div class="preview-pane">
    <div class="pv-bar">
      <div class="pv-seg" role="group" aria-label="预览版本">
        <button :class="{ on: src === 'archive' }" @click="switchSrc('archive')">归档版</button>
        <button :class="{ on: src === 'original' }" @click="switchSrc('original')">拍摄原件</button>
      </div>

      <div v-if="pageCount > 1" class="pv-pager">
        <button class="pv-btn" :disabled="page <= 1" aria-label="上一页" @click="goto(page - 1)">‹</button>
        <input
          v-model="pageInput" class="pv-page-input" inputmode="numeric"
          aria-label="页码" @keydown.enter="jump" @blur="jump" />
        <span class="pv-page-total">/ {{ pageCount }}</span>
        <button class="pv-btn" :disabled="page >= pageCount" aria-label="下一页" @click="goto(page + 1)">›</button>
      </div>
      <span v-else class="pv-muted pv-single">单页</span>

      <div class="pv-actions">
        <button class="pv-btn" :disabled="!imgUrl" @click="openZoom">放大</button>
        <button class="pv-btn" @click="doDownload">下载{{ src === 'original' ? '原件' : '归档版' }}</button>
      </div>
    </div>

    <div class="pv-stage">
      <div v-if="loading && !imgUrl" class="pv-placeholder"><span class="spinner"></span> 正在生成预览…</div>
      <div v-if="err" class="pv-error">
        ⚠ {{ err }}
        <div class="pv-error-hint">可尝试切换「拍摄原件 / 归档版」，或直接下载查看。</div>
      </div>
      <img
        v-if="imgUrl" :src="imgUrl" class="pv-img" :class="{ dim: loading }"
        alt="档案原件预览" @click="openZoom" />
    </div>

    <p class="pv-foot">
      在线预览由服务端渲染为图片，不依赖浏览器 PDF 插件；多页文档可翻页，点击图片放大。
      <span v-if="originalMime" class="pv-muted">原件类型：{{ originalMime }}</span>
    </p>

    <!-- 全屏放大层：移动端也可双指缩放，这里提供按钮与点击关闭兜底 -->
    <div v-if="zoomed" class="pv-overlay" @click.self="closeZoom">
      <div class="pv-overlay-bar">
        <button class="pv-btn" @click="bump(-0.3)">− 缩小</button>
        <span class="pv-muted">{{ Math.round(scale * 100) }}%</span>
        <button class="pv-btn" @click="bump(0.3)">＋ 放大</button>
        <button class="pv-btn" @click="closeZoom">关闭</button>
      </div>
      <div class="pv-overlay-body" @click.self="closeZoom">
        <img :src="imgUrl" alt="放大预览" :style="{ transform: `scale(${scale})` }" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.preview-pane { display: flex; flex-direction: column; gap: 10px; }
.pv-bar {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding-bottom: 10px; border-bottom: 1px solid var(--border, #e3e8ef);
}
.pv-seg { display: inline-flex; border: 1px solid var(--border, #d8e0ea); border-radius: 8px; overflow: hidden; }
.pv-seg button {
  min-height: 38px; padding: 0 14px; border: 0; background: transparent;
  color: var(--muted, #5b6b7c); font-size: 13px; cursor: pointer;
}
.pv-seg button.on { background: var(--accent, #1d5b9e); color: #fff; font-weight: 600; }
.pv-pager { display: inline-flex; align-items: center; gap: 6px; }
.pv-btn {
  min-height: 38px; min-width: 38px; padding: 0 12px;
  border: 1px solid var(--border, #d8e0ea); border-radius: 8px;
  background: #fff; color: var(--text, #1b2a3a); font-size: 13px; cursor: pointer;
}
.pv-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.pv-page-input {
  width: 52px; min-height: 38px; text-align: center;
  border: 1px solid var(--border, #d8e0ea); border-radius: 8px; font-size: 13px;
}
.pv-page-total, .pv-muted { font-size: 12px; color: var(--muted, #5b6b7c); }
.pv-actions { display: inline-flex; gap: 8px; margin-left: auto; }
.pv-stage {
  position: relative; min-height: 220px; background: #f4f6f9;
  border: 1px solid var(--border, #e3e8ef); border-radius: 10px;
  display: flex; align-items: center; justify-content: center; overflow: hidden;
}
.pv-img { display: block; width: 100%; height: auto; max-height: 72vh; object-fit: contain; cursor: zoom-in; }
.pv-img.dim { opacity: 0.45; }
.pv-placeholder { color: var(--muted, #5b6b7c); font-size: 13px; padding: 40px 0; }
.pv-error { padding: 18px; color: #92400e; font-size: 13px; line-height: 1.7; text-align: center; }
.pv-error-hint { margin-top: 6px; color: var(--muted, #5b6b7c); font-size: 12px; }
.pv-foot { margin: 0; font-size: 12px; line-height: 1.7; color: var(--muted, #5b6b7c); }
.pv-foot .pv-muted { display: block; margin-top: 2px; }

.pv-overlay {
  position: fixed; inset: 0; z-index: 9999; background: rgba(12, 20, 30, 0.94);
  display: flex; flex-direction: column;
  padding-top: env(safe-area-inset-top); padding-bottom: env(safe-area-inset-bottom);
}
.pv-overlay-bar {
  display: flex; align-items: center; justify-content: center; gap: 10px;
  padding: 10px 14px; flex-wrap: wrap;
}
.pv-overlay-bar .pv-btn { background: rgba(255,255,255,0.12); color: #fff; border-color: rgba(255,255,255,0.25); }
.pv-overlay-bar .pv-muted { color: #cbd6e2; }
.pv-overlay-body { flex: 1; overflow: auto; display: flex; align-items: flex-start; justify-content: center; }
.pv-overlay-body img {
  max-width: 100%; height: auto; transform-origin: top center;
  transition: transform 0.15s ease;
}

@media (max-width: 640px) {
  .pv-bar { gap: 8px; }
  /* 版本切换独占一行，避免「单页」标签被挤到屏幕右边缘 */
  .pv-seg { flex: 1 1 100%; }
  .pv-seg button { flex: 1; padding: 0 8px; }
  .pv-single { display: none; }
  .pv-actions { margin-left: 0; width: 100%; }
  .pv-actions .pv-btn { flex: 1; }
  .pv-img { max-height: 60vh; }
  .pv-btn, .pv-seg button, .pv-page-input { min-height: 42px; }
}
</style>
