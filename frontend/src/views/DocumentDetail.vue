<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, CLASS_NAMES, downloadFile, getUser, ocrReprocess, patchContent } from '../api'
import PreviewPane from '../components/PreviewPane.vue'

const route = useRoute()
const doc = ref<any>(null)
const err = ref('')
const busy = ref(true)
const downloading = ref(false)
const actionErr = ref('')

const ocrEmpty = computed(() => !doc.value || !(doc.value.content || '').trim())
const canEditContent = computed(() => {
  const u = getUser()
  return !!u && (u.roles || []).some((r: string) => r === 'archivist' || r === 'admin')
})

// 原件没有印刷文字时，允许人工补录正文并触发语义重建
const editing = ref(false)
const draft = ref('')
const saving = ref(false)
const contentMsg = ref('')

function startEdit() {
  draft.value = doc.value?.content || ''
  editing.value = true
  contentMsg.value = ''
}

async function saveContent() {
  if (saving.value) return
  saving.value = true
  contentMsg.value = ''
  try {
    const r = await patchContent(String(route.params.id), draft.value)
    doc.value = { ...doc.value, content: draft.value }
    editing.value = false
    contentMsg.value = `已保存（${r.content_length} 字），语义索引正在后台重建，稍后即可被全文与语义检索命中。`
  } catch (e: any) {
    contentMsg.value = e.message
  } finally {
    saving.value = false
  }
}

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

// 强制用本地 PaddleOCR 对原件重识别（手写 / 潦草笔记专用）
const reprocessing = ref(false)
const reprocessMsg = ref('')
async function reprocess() {
  if (reprocessing.value) return
  reprocessing.value = true
  reprocessMsg.value = ''
  try {
    const r = await ocrReprocess(String(route.params.id))
    doc.value = await api<any>(`/api/v1/documents/${route.params.id}`)
    reprocessMsg.value = r.content_length > 0
      ? `PaddleOCR 已对手写/潦草原件重识别，写入 ${r.content_length} 字，语义索引正在后台重建。`
      : 'PaddleOCR 仍未识别到文字（原件可能无手写笔画或过于潦草）。如需检索，可在下方「OCR 全文」卡片补录一段描述。'
  } catch (e: any) {
    reprocessMsg.value = e.message
  } finally {
    reprocessing.value = false
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
          <button v-if="canEditContent" class="btn primary" :disabled="reprocessing" @click="reprocess">
            {{ reprocessing ? '识别中…' : '手写OCR重处理' }}
          </button>
        </div>
        <p v-if="reprocessMsg" class="content-msg">{{ reprocessMsg }}</p>
        <p v-if="actionErr" class="err inline-err">{{ actionErr }}</p>
        <p v-if="ocrEmpty" class="ocr-empty">
          ⚠ 该原件未提取到 OCR 文字（正文 0 字）。<strong>档案与拍摄原件都已完整入库，这不是内容丢失。</strong>
          页面上这些中文是界面文字，不参与识别；OCR 只处理你上传的那张图。若图中是浅色压纹、翻拍屏幕或照片图案，
          笔画灰度与背景过近，二值化时会被一起判为白色，识别引擎只输出乱码碎片，系统按预期丢弃低置信度结果。
          <br />可选处理：① 改拍纸质原件或直接上传屏幕截图重新归档；② 在下方「OCR 全文」卡片补录一段描述，保存后即可被检索。
        </p>
      </div>

      <div class="detail-grid">
        <div class="card pv-card">
          <h3 class="card-title">📄 原件预览</h3>
          <PreviewPane :doc-id="String(route.params.id)" />
        </div>
        <div class="card">
          <div class="row ocr-head">
            <h3 class="card-title">🔤 OCR 全文</h3>
            <button v-if="canEditContent && !editing" class="btn" @click="startEdit">
              {{ ocrEmpty ? '补录正文' : '编辑正文' }}
            </button>
          </div>
          <template v-if="editing">
            <textarea
              v-model="draft"
              class="content-editor"
              rows="10"
              placeholder="手动补录档案正文或摘要，例如：瑞幸咖啡纸袋压纹照片，含品牌 logo 与几何纹样……"
            ></textarea>
            <div class="row editor-actions">
              <button class="btn btn-save" :disabled="saving" @click="saveContent">
                {{ saving ? '保存中…' : '保存并重索引' }}
              </button>
              <button class="btn" :disabled="saving" @click="editing = false">取消</button>
            </div>
          </template>
          <pre v-else class="ocr-text">{{ doc.content || '（暂无 OCR 文本，请查看左侧原件预览）' }}</pre>
          <p v-if="contentMsg" class="content-msg">{{ contentMsg }}</p>
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
.btn.primary { background: #1a6fc4; color: #fff; border-color: #1a6fc4; }
.btn.primary:hover { background: #155a9e; }
.inline-err { margin: 10px 0 0; font-size: 13px; }
.ocr-empty {
  margin: 12px 0 0; padding: 9px 11px; font-size: 12px; line-height: 1.7;
  color: #92400e; background: #fffbeb; border-left: 3px solid var(--warn, #d99b2b);
  border-radius: 6px;
}
.ocr-head { justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.ocr-head .card-title { margin: 0; }
.ocr-head .btn { min-height: 34px; }
.content-editor {
  width: 100%; box-sizing: border-box; min-height: 180px; resize: vertical;
  padding: 10px 12px; font: inherit; font-size: 13px; line-height: 1.7;
  border: 1px solid var(--line, #d8e0e8); border-radius: 8px;
  background: #fff; color: inherit;
}
.content-editor:focus { outline: 2px solid var(--brand, #1a6fc4); outline-offset: 1px; }
.editor-actions { gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.btn-save { background: var(--brand, #1a6fc4); color: #fff; border-color: transparent; font-weight: 600; }
.btn-save:disabled { opacity: .6; }
.content-msg { margin: 10px 0 0; font-size: 12px; line-height: 1.6; color: #166534; }
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
  .ocr-head .btn,
  .editor-actions .btn { flex: 1; min-height: 42px; }
  .content-editor { min-height: 150px; }
}
</style>
