<script setup lang="ts">
/** 独立预览页：新窗口打开时无全局顶栏，纯图片画廊。
 *  与详情页共用 PreviewPane，同样不依赖浏览器 PDF 插件。 */
import { useRoute, useRouter } from 'vue-router'
import PreviewPane from '../components/PreviewPane.vue'

const route = useRoute()
const router = useRouter()

function close() {
  // 由详情页 window.open 打开的窗口可直接关闭；否则退回详情页
  window.close()
  setTimeout(() => router.push(`/doc/${route.params.id}`), 200)
}
</script>

<template>
  <div class="standalone">
    <header class="sa-bar">
      <span class="sa-brand">档枢 <b>ArcHub</b> · 原件预览</span>
      <span class="sa-doc">档案 #{{ route.params.id }}</span>
      <button class="btn" @click="close">关闭</button>
    </header>
    <main class="sa-body">
      <PreviewPane :doc-id="String(route.params.id)" />
    </main>
  </div>
</template>

<style scoped>
.standalone {
  position: fixed; inset: 0; z-index: 100; overflow: auto;
  background: var(--bg, #f5f7fa);
  padding-top: env(safe-area-inset-top);
  padding-bottom: env(safe-area-inset-bottom);
}
.sa-bar {
  position: sticky; top: 0; z-index: 2;
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 10px 16px;
  background: linear-gradient(100deg, #0e2a47 0%, #173b5e 100%);
  border-bottom: 2px solid var(--gold, #c69a4b);
  color: #fff;
}
.sa-brand { font-size: 15px; }
.sa-brand b { color: var(--gold, #c69a4b); }
.sa-doc { font-size: 12px; color: #cbd6e2; }
.sa-bar .btn { margin-left: auto; min-height: 38px; }
.sa-body { max-width: 1000px; margin: 0 auto; padding: 16px; }
@media (max-width: 640px) {
  .sa-bar { padding: 10px 12px; gap: 8px; }
  .sa-body { padding: 12px; }
  .sa-bar .btn { min-height: 42px; }
}
</style>
