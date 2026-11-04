<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, CLASS_NAMES } from '../api'

const route = useRoute()
const doc = ref<any>(null)
const err = ref('')
const busy = ref(true)

onMounted(async () => {
  try {
    doc.value = await api<any>(`/api/v1/documents/${route.params.id}`)
  } catch (e: any) {
    err.value = e.message
  } finally {
    busy.value = false
  }
})
</script>

<template>
  <div class="layout">
    <p v-if="busy" class="muted"><span class="spinner"></span> 加载中…</p>
    <p v-if="err" class="card err">{{ err }}</p>
    <template v-if="doc">
      <div class="card">
        <div class="row" style="justify-content: space-between">
          <h2 style="margin: 0">{{ doc.title || '（无题名）' }}</h2>
          <span>
            <span v-if="doc.archive_serial_number" class="badge">DA-{{ doc.archive_serial_number }}</span>
            <span class="badge warn" style="margin-left: 6px">{{
              CLASS_NAMES[doc.classification ?? 1]
            }}</span>
          </span>
        </div>
        <div class="row muted" style="margin-top: 8px; font-size: 13px">
          <span>形成日期: {{ (doc.created || '').slice(0, 10) }}</span>
          <span>责任者 ID: {{ doc.correspondent || '-' }}</span>
          <a :href="`/api/v1/documents/${doc.id}/download?inline=true`" target="_blank">新窗口预览</a>
          <a :href="`/api/v1/documents/${doc.id}/download`">下载原件</a>
        </div>
      </div>
      <div class="detail-grid">
        <div class="card">
          <h3 style="margin-top: 0">📄 原件预览</h3>
          <iframe
            :src="`/api/v1/documents/${doc.id}/download?inline=true`"
            style="width: 100%; height: 640px; border: 1px solid var(--border); border-radius: 8px"
          ></iframe>
        </div>
        <div class="card">
          <h3 style="margin-top: 0">🔤 OCR 全文</h3>
          <pre class="ocr-text">{{ doc.content || '（暂无 OCR 文本）' }}</pre>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.detail-grid {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 14px;
}
@media (max-width: 900px) {
  .detail-grid { grid-template-columns: 1fr; }
}
.ocr-text {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 13px;
  line-height: 1.7;
  max-height: 640px;
  overflow: auto;
  font-family: inherit;
  margin: 0;
}
.err { color: var(--danger); }
</style>
