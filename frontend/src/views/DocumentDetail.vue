<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, CLASS_NAMES, downloadFile } from '../api'
import PreviewPane from '../components/PreviewPane.vue'

const route = useRoute()
const doc = ref<any>(null)
const err = ref('')
const busy = ref(true)
const downloading = ref(false)
const actionErr = ref('')

const ocrEmpty = computed(() => !doc.value || !(doc.value.content || '').trim())

onMounted(async () => {
  try {
    doc.value = await api<any>(`/api/v1/documents/${route.params.id}`)
  } catch (e: any) {
    err.value = e.message
  } finally {
    busy.value = false
  }
})

/** 独立预览页保留 location.search（公网平台需要 sharecode），只替换 hash 路由。 */
function openStandalone() {
  const base = location.origin + location.pathname + location.search
  window.open(`${base}#/preview/${route.params.id}`, '_blank', 'noopener')
}

async function download(original: boolean) {
  if (downloading.value) return
  downloading.value = true
  actionErr.value = ''
  try {
    await downloadFile(
      `/api/v1/documents/${route.params.id}/download?original=${original}`,
      `arhub-${route.params.id}-${original ? 'original.jpg' : 'archive.pdf'}`)
  } catch (e: any) {
    actionErr.value = e.message
  } finally {
    downloading.value = false
  }
}
</script>

<template>
  <div class="layout">
    <p v-if="busy" class="muted"><span class="spinner"></span> 加载中…</p>
    <p v-if="err" class="card err">{{ err }}</p>
    <template v-if="doc">
      <div class="card">
        <div class="row head-row">
          <h2 class="doc-title">{{ doc.title || '（无题名）' }}</h2>
          <span class="badges">
            <span v-if="doc.archive_serial_number" class="badge">DA-{{ doc.archive_serial_number }}</span>
            <span class="badge warn">{{ CLASS_NAMES[doc.classification ?? 1] }}</span>
          </span>
        </div>
        <div class="row muted meta-row">
          <span>形成日期: {{ String(doc.created || '').slice(0, 10) }}</span>
          <span>责任者 ID: {{ doc.correspondent || '-' }}</span>
        </div>
        <div class="row action-row">
          <button class="btn" @click="openStandalone">新窗口预览</button>
          <button class="btn" :disabled="downloading" @click="download(false)">下载归档 PDF</button>
          <button class="btn" :disabled="downloading" @click="download(true)">下载原件</button>
        </div>
        <p v-if="actionErr" class="err inline-err">{{ actionErr }}</p>
        <p v-if="ocrEmpty" class="ocr-empty">
          ⚠ 未识别到文字（正文 0 字）。该档案已入库，下方仍可查看拍摄原件；建议改拍纸质原件、
          直接上传屏幕截图，或在采集页用「仅裁边校正 / 原图」重新归档。
        </p>
      </div>

      <div class="detail-grid">
        <div class="card pv-card">
          <h3 class="card-title">📄 原件预览</h3>
          <PreviewPane :doc-id="String(route.params.id)" />
        </div>
        <div class="card">
          <h3 class="card-title">🔤 OCR 全文</h3>
          <pre class="ocr-text">{{ doc.content || '（暂无 OCR 文本，请查看左侧原件预览）' }}</pre>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.head-row { justify-content: space-between; align-items: flex-start; gap: 10px; }
.doc-title { margin: 0; font-size: 19px; line-height: 1.45; word-break: break-word; }
.badges { flex: none; white-space: nowrap; }
.badges .warn { margin-left: 6px; }
.meta-row { margin-top: 8px; font-size: 13px; gap: 14px; flex-wrap: wrap; }
.action-row { margin-top: 12px; gap: 8px; flex-wrap: wrap; }
.action-row .btn { min-height: 38px; }
.inline-err { margin: 10px 0 0; font-size: 13px; }
.ocr-empty {
  margin: 12px 0 0; padding: 9px 11px; font-size: 12px; line-height: 1.7;
  color: #92400e; background: #fffbeb; border-left: 3px solid var(--warn, #d99b2b);
  border-radius: 6px;
}
.detail-grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 14px; margin-top: 14px; }
.card-title { margin: 0 0 12px; font-size: 15px; }
.pv-card { min-width: 0; }
.ocr-text {
  white-space: pre-wrap; word-break: break-all; font-size: 13px; line-height: 1.7;
  max-height: 640px; overflow: auto; font-family: inherit; margin: 0;
}
.err { color: var(--danger, #b42318); }
@media (max-width: 900px) {
  .detail-grid { grid-template-columns: 1fr; }
}
@media (max-width: 640px) {
  .head-row { flex-direction: column; align-items: flex-start; }
  .action-row .btn { flex: 1; min-height: 42px; }
  .doc-title { font-size: 17px; }
}
</style>
