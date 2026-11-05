<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const q = ref('')
const mode = ref('hybrid')
const busy = ref(false)
const err = ref('')
const results = ref<any[]>([])
const total = ref(0)
const degraded = ref(false)
const browsing = ref(false)

// 每页条数 / 检索召回上限。此前是原生 <select>，最大只有 50，
// 且浏览模式没有分页，用户会以为「总的内容只能显示 50 条」。
const PAGE_SIZES = [5, 10, 20, 50, 100]
const pageSize = ref(20)
const page = ref(1)
const totalPages = ref(0)
const hasMore = ref(false)

const meta = reactive({ asn: '', dateFrom: '', dateTo: '', corr: '', dtype: '', tag: '' })

const MODES = [
  { key: 'hybrid', label: '混合', hint: '关键词+语义融合，召回率最高，默认推荐' },
  { key: 'keyword', label: '关键词', hint: 'OCR 全文字面匹配' },
  { key: 'semantic', label: '语义', hint: '大白话提问，不要求措辞一致' },
  { key: 'metadata', label: '元数据', hint: '按档号/日期/责任者/门类精确过滤' },
]

const pageButtons = computed(() => {
  const n = totalPages.value
  if (n <= 7) return Array.from({ length: n }, (_, i) => i + 1)
  let start = Math.max(1, page.value - 2)
  let end = Math.min(n, start + 4)
  start = Math.max(1, end - 4)
  return Array.from({ length: end - start + 1 }, (_, i) => start + i)
})

async function search(): Promise<void> {
  err.value = ''
  const p = new URLSearchParams({
    mode: mode.value,
    top_k: String(pageSize.value),
    page_size: String(pageSize.value),
  })
  if (mode.value === 'metadata') {
    if (meta.asn) p.set('asn', meta.asn)
    if (meta.dateFrom) p.set('created_after', meta.dateFrom)
    if (meta.dateTo) p.set('created_before', meta.dateTo)
    if (meta.corr) p.set('correspondent', meta.corr)
    if (meta.dtype) p.set('document_type', meta.dtype)
    if (meta.tag) p.set('tag', meta.tag)
  } else {
    if (q.value.trim()) p.set('q', q.value.trim())
    else p.set('page', String(page.value))  // 浏览模式分页
  }
  // 空检索词不再直接 return：后端切换为浏览模式，返回全部可见档案 + 真实总数。
  busy.value = true
  try {
    const res = await api<any>(`/api/v1/search?${p}`)
    results.value = res.results || []
    total.value = res.total ?? results.value.length
    degraded.value = !!res.degraded
    browsing.value = !!res.browsing
    totalPages.value = res.total_pages || 0
    hasMore.value = !!res.has_more
    // 档案被删除导致当前页越界时自动回退，避免空白页
    if (browsing.value && totalPages.value > 0 && page.value > totalPages.value) {
      page.value = totalPages.value
      busy.value = false
      return search()
    }
  } catch (e: any) {
    err.value = e.message
    results.value = []
    browsing.value = false
  } finally {
    busy.value = false
  }
}

function doSearch() { page.value = 1; search() }
function setPageSize(n: number) {
  if (pageSize.value === n) return
  pageSize.value = n
  page.value = 1
  search()
}
function gotoPage(p: number) {
  if (busy.value || p < 1 || (totalPages.value && p > totalPages.value) || p === page.value) return
  page.value = p
  search()
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

// v-html 白名单清洗：仅保留 <mark> 高亮标签，其余全部剥离（防 XSS）
function sanitized(html: string): string {
  const div = document.createElement('div')
  div.textContent = html
  return div.textContent!
    .replace(/&lt;mark&gt;/g, '<mark>')
    .replace(/&lt;\/mark&gt;/g, '</mark>')
}

onMounted(search)
</script>

<template>
  <div class="layout">
    <div class="card">
      <div class="row search-row">
        <input
          v-model="q" placeholder="输入检索词，支持大白话提问…"
          class="q-input" @keydown.enter="doSearch" />
        <button class="btn primary" :disabled="busy" @click="doSearch">
          <span v-if="busy" class="spinner"></span> 检索
        </button>
      </div>
      <div class="row" style="margin-top: 10px">
        <span
          v-for="m in MODES" :key="m.key" class="mode-chip"
          :class="{ on: mode === m.key }" @click="mode = m.key">{{ m.label }}</span>
        <span class="muted">{{ MODES.find((x) => x.key === mode)?.hint }}</span>
      </div>
      <div class="row size-row">
        <span class="muted">{{ browsing ? '每页' : '最多返回' }}</span>
        <div class="seg" role="group" aria-label="每页条数">
          <button
            v-for="n in PAGE_SIZES" :key="n" :class="{ on: pageSize === n }"
            @click="setPageSize(n)">{{ n }}</button>
        </div>
        <span class="muted">条</span>
        <span class="muted size-hint">
          {{ browsing ? '档案库无条数上限，可翻页浏览全部' : '检索召回上限，非档案总量' }}
        </span>
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
      <span v-if="browsing">
        共 <b>{{ total }}</b> 份档案 · 第 {{ page }}/{{ totalPages || 1 }} 页
        <span class="muted">· 输入检索词开始全文检索</span>
      </span>
      <span v-else>共 <b>{{ total }}</b> 条结果</span>
      <span class="muted">{{ browsing ? '按入库时间倒序' : '按相关度排序' }}</span>
    </div>

    <div v-for="(r, i) in results" :key="r.doc_id" class="card hit">
      <div class="row" style="justify-content: space-between">
        <span>
          <b>{{ (browsing ? (page - 1) * pageSize + i : i) + 1 }}. {{ r.title || '（无题名）' }}</b>
          <span v-if="r.asn" class="badge" style="margin-left: 8px">DA-{{ r.asn }}</span>
          <span v-if="r.classification != null" class="badge warn" style="margin-left: 6px">{{
            ['公开', '内部', '秘密', '机密'][r.classification]
          }}</span>
        </span>
        <span class="muted">{{ String(r.created || '').slice(0, 10) }}</span>
      </div>
      <!-- OCR 为空必须显式告知：档案已入库，但内容没识别出来，不是「内容丢失」 -->
      <p v-if="r.ocr_empty" class="ocr-empty">
        ⚠ 未识别到文字（正文 {{ r.content_length ?? 0 }} 字）。该档案已入库，原件可能是浅色压纹、
        翻拍屏幕或图案照片，本身没有可识别的印刷笔画；建议重拍，或在详情页「补录正文」写一段描述使其可被检索。
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

    <!-- 浏览模式分页：库内档案全部可翻到，不再有 50 条天花板 -->
    <div v-if="browsing && totalPages > 1" class="pager card">
      <button class="pg-btn" :disabled="page <= 1 || busy" @click="gotoPage(page - 1)">‹ 上一页</button>
      <button
        v-for="p in pageButtons" :key="p" class="pg-btn pg-num"
        :class="{ on: p === page }" @click="gotoPage(p)">{{ p }}</button>
      <button class="pg-btn" :disabled="page >= totalPages || busy" @click="gotoPage(page + 1)">下一页 ›</button>
      <span class="muted pg-info">共 {{ total }} 份 · {{ totalPages }} 页{{ hasMore ? ' · 还有更多' : '' }}</span>
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

<style scoped>
.search-row .q-input { flex: 1; min-width: 200px; }
.mode-chip {
  padding: 4px 12px; border: 1px solid var(--border, #d8e0ea);
  border-radius: 14px; cursor: pointer; font-size: 13px;
}
.mode-chip.on { background: var(--accent, #1d5b9e); border-color: var(--accent, #1d5b9e); color: #fff; }
.size-row { margin-top: 10px; gap: 8px; flex-wrap: wrap; align-items: center; font-size: 13px; }
.seg { display: inline-flex; border: 1px solid var(--border, #d8e0ea); border-radius: 8px; overflow: hidden; }
.seg button {
  min-height: 36px; min-width: 44px; padding: 0 12px; border: 0;
  border-right: 1px solid var(--border, #d8e0ea);
  background: #fff; color: var(--muted, #5b6b7c); font-size: 13px; cursor: pointer;
}
.seg button:last-child { border-right: 0; }
.seg button.on { background: var(--accent, #1d5b9e); color: #fff; font-weight: 600; }
.size-hint { font-size: 12px; }
.result-bar {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  flex-wrap: wrap; margin-bottom: 8px; font-size: 13px; color: var(--muted, #5b6b7c);
}
.result-bar b { color: var(--text, #1b2a3a); }
.hit .snip {
  background: #fafbfc; border-left: 3px solid var(--accent, #1d5b9e);
  padding: 8px 10px; border-radius: 6px; font-size: 13px; margin: 10px 0;
  word-break: break-all;
}
.ocr-empty {
  margin: 10px 0 0; padding: 8px 10px; font-size: 12px; line-height: 1.7;
  color: #92400e; background: #fffbeb; border-left: 3px solid var(--warn, #d99b2b);
  border-radius: 6px;
}
.pager { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; justify-content: center; }
.pg-btn {
  min-height: 38px; padding: 0 12px; border: 1px solid var(--border, #d8e0ea);
  border-radius: 8px; background: #fff; color: var(--text, #1b2a3a);
  font-size: 13px; cursor: pointer;
}
.pg-btn.on { background: var(--accent, #1d5b9e); border-color: var(--accent, #1d5b9e); color: #fff; font-weight: 600; }
.pg-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.pg-num { min-width: 38px; }
.pg-info { flex-basis: 100%; text-align: center; font-size: 12px; margin-top: 4px; }
.empty-box { text-align: center; line-height: 1.8; }
.empty-box b { color: var(--accent, #1d5b9e); }
.warn-line { color: var(--warn, #d99b2b); font-size: 13px; }
.err { color: var(--danger, #b42318); font-size: 13px; }
@media (max-width: 640px) {
  .search-row .q-input { min-width: 0; }
  .search-row .btn.primary { min-height: 42px; }
  .seg button { min-height: 42px; min-width: 48px; }
  .pg-btn { min-height: 42px; }
  .size-hint { flex-basis: 100%; }
}
</style>
