<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const q = ref('')
const mode = ref('hybrid')
const busy = ref(false)
const err = ref('')
const results = ref<any[]>([])
const total = ref(0)
const degraded = ref(false)
const browsing = ref(false)
const topk = ref(10)
const meta = reactive({ asn: '', dateFrom: '', dateTo: '', corr: '', dtype: '', tag: '' })

const MODES = [
  { key: 'hybrid', label: '混合', hint: '关键词+语义融合，召回率最高，默认推荐' },
  { key: 'keyword', label: '关键词', hint: 'OCR 全文字面匹配' },
  { key: 'semantic', label: '语义', hint: '大白话提问，不要求措辞一致' },
  { key: 'metadata', label: '元数据', hint: '按档号/日期/责任者/门类精确过滤' },
]

async function search() {
  err.value = ''
  const p = new URLSearchParams({ mode: mode.value, top_k: String(topk.value) })
  if (mode.value === 'metadata') {
    if (meta.asn) p.set('asn', meta.asn)
    if (meta.dateFrom) p.set('created_after', meta.dateFrom)
    if (meta.dateTo) p.set('created_before', meta.dateTo)
    if (meta.corr) p.set('correspondent', meta.corr)
    if (meta.dtype) p.set('document_type', meta.dtype)
    if (meta.tag) p.set('tag', meta.tag)
  } else if (q.value.trim()) {
    p.set('q', q.value.trim())
  }
  // 注意：空检索词不再直接 return。此前首次进入检索页会显示「暂无结果」，
  // 让人误以为档案库是空的或采集的文档内容丢失。后端在空 q 时切换为
  // 浏览模式，返回全部可见档案并标注 OCR 状态。
  busy.value = true
  try {
    const res = await api<any>(`/api/v1/search?${p}`)
    results.value = res.results || []
    total.value = res.total
    degraded.value = res.degraded
    browsing.value = !!res.browsing
  } catch (e: any) {
    err.value = e.message
    results.value = []
    browsing.value = false
  } finally {
    busy.value = false
  }
}

onMounted(search)
</script>

<template>
  <div class="layout">
    <div class="card">
      <div class="row">
        <input
          v-model="q"
          placeholder="输入检索词，支持大白话提问…"
          style="flex: 1; min-width: 240px"
          @keydown.enter="search"
        />
        <select v-model="topk" style="width: 90px">
          <option :value="5">5 条</option>
          <option :value="10">10 条</option>
          <option :value="20">20 条</option>
          <option :value="50">50 条</option>
        </select>
        <button class="btn primary" :disabled="busy" @click="search">
          <span v-if="busy" class="spinner"></span> 检索
        </button>
      </div>
      <div class="row" style="margin-top: 10px">
        <span
          v-for="m in MODES"
          :key="m.key"
          class="mode-chip"
          :class="{ on: mode === m.key }"
          @click="mode = m.key"
          >{{ m.label }}</span
        >
        <span class="muted">{{ MODES.find((x) => x.key === mode)?.hint }}</span>
      </div>
      <div v-if="mode === 'metadata'" class="row" style="margin-top: 10px">
        <input v-model="meta.asn" placeholder="档号" style="width: 90px" />
        <input v-model="meta.dateFrom" type="date" />
        <input v-model="meta.dateTo" type="date" />
        <input v-model="meta.corr" placeholder="责任者" style="width: 110px" />
        <input v-model="meta.dtype" placeholder="门类" style="width: 100px" />
        <input v-model="meta.tag" placeholder="标签" style="width: 100px" />
      </div>
      <p v-if="degraded" class="warn-line">⚠ 语义服务暂不可用，当前为关键词降级结果</p>
      <p v-if="err" class="err">{{ err }}</p>
    </div>

    <div v-if="results.length" class="result-bar">
      <span v-if="browsing">共 <b>{{ total }}</b> 份档案 · 输入检索词开始全文检索</span>
      <span v-else>共 <b>{{ total }}</b> 条结果</span>
      <span v-if="browsing" class="muted">按入库时间倒序</span>
    </div>
    <div v-for="(r, i) in results" :key="r.doc_id" class="card hit">
      <div class="row" style="justify-content: space-between">
        <span>
          <b>{{ i + 1 }}. {{ r.title || '（无题名）' }}</b>
          <span v-if="r.asn" class="badge" style="margin-left: 8px">DA-{{ r.asn }}</span>
          <span v-if="r.classification != null" class="badge warn" style="margin-left: 6px">{{
            ['公开', '内部', '秘密', '机密'][r.classification]
          }}</span>
        </span>
        <span class="muted">{{ (r.created || '').slice(0, 10) }}</span>
      </div>
      <!-- OCR 为空必须显式告知：档案已入库，但内容没识别出来，不是「内容丢失」 -->
      <p v-if="r.ocr_empty" class="ocr-empty">
        ⚠ 未识别到文字（正文 {{ r.content_length ?? 0 }} 字）。该档案已入库，但可能是空白页、
        翻拍屏幕或文字过淡；建议重拍，或在采集页改用「仅裁边校正 / 原图」重新上传。
      </p>
      <p v-if="r.snippet" class="snip">{{ r.snippet }}</p>
      <p v-if="r.highlights" class="snip" style="border-left-color: var(--warn)">
        <span v-html="sanitized(r.highlights)"></span>
      </p>
      <div class="row muted" style="font-size: 12px">
        <span>{{ browsing ? '浏览' : '命中: ' + (r.matched_by || [r.source]).join(' + ') }}</span>
        <span v-if="r.rrf_score">RRF {{ r.rrf_score }}</span>
        <span v-else-if="r.score">score {{ r.score }}</span>
        <router-link :to="`/doc/${r.doc_id}`" style="margin-left: auto">查看详情 →</router-link>
      </div>
    </div>
    <div v-if="!busy && !results.length && !err" class="card muted empty-box">
      <template v-if="browsing">
        档案库还是空的，去<b>采集扫描</b>添加第一份档案吧
      </template>
      <template v-else>
        暂无结果，请调整检索条件（换个关键词、放宽模式或清空检索词查看全部档案）
      </template>
    </div>
  </div>
</template>

<script lang="ts">
// v-html 白名单清洗：仅保留 <mark> 高亮标签，其余全部剥离（防 XSS）
function sanitized(html: string): string {
  const div = document.createElement('div')
  div.textContent = html
  return div
    .textContent!.replace(/&lt;mark&gt;/g, '<mark>')
    .replace(/&lt;\/mark&gt;/g, '</mark>')
}
export default { methods: { sanitized } }
</script>

<style scoped>
.mode-chip {
  padding: 4px 12px;
  border: 1px solid var(--border);
  border-radius: 14px;
  cursor: pointer;
  font-size: 13px;
}
.mode-chip.on {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.result-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--muted);
}
.result-bar b { color: var(--text); }
.hit .snip {
  background: #fafbfc;
  border-left: 3px solid var(--accent);
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 13px;
  margin: 10px 0;
  word-break: break-all;
}
.ocr-empty {
  margin: 10px 0 0;
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.7;
  color: #92400e;
  background: #fffbeb;
  border-left: 3px solid var(--warn);
  border-radius: 6px;
}
.empty-box { text-align: center; line-height: 1.8; }
.empty-box b { color: var(--accent); }
.warn-line { color: var(--warn); font-size: 13px; }
.err { color: var(--danger); font-size: 13px; }
</style>
